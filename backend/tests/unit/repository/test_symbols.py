from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from contracts import RepositoryChange
from lou.core.errors import GitExecutionError, InvalidCommitError
from lou.repository import extract_changed_symbols, parse_repository_changes
from lou.repository import symbols as symbols_module


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "--allow-empty", "-m", "test snapshot")
    return _git(repository, "rev-parse", "HEAD")


def _change(repository: Path, base: str, candidate: str) -> RepositoryChange:
    return parse_repository_changes(
        repository_id="test-repository",
        repository_path=repository,
        base_revision=base,
        candidate_revision=candidate,
    )


def _extract(repository: Path, base: str, candidate: str) -> RepositoryChange:
    return extract_changed_symbols(
        repository_path=repository, change=_change(repository, base, candidate)
    )


def test_method_changes_preserve_contract_and_ignore_worktree(git_repository: Path) -> None:
    path = git_repository / "store.py"
    path.write_text(
        "class First:\n    def checkout(self):\n        return 1\n\n"
        "class Second:\n    def checkout(self):\n        return 2\n"
    )
    base = _commit(git_repository)
    path.write_text(path.read_text().replace("return 1", "return 3"))
    candidate = _commit(git_repository)
    path.write_text("raise RuntimeError('never import repository source')\n")
    change = _change(git_repository, base, candidate)
    change.metadata["caller"] = {"retained": True}
    before = change.model_dump_json()

    result = extract_changed_symbols(repository_path=git_repository, change=change)

    assert result.changed_symbols == ["store.First.checkout"]
    assert result.completeness == 1
    assert change.model_dump_json() == before
    assert RepositoryChange.model_validate_json(result.model_dump_json()) == result
    assert extract_changed_symbols(repository_path=git_repository, change=change) == result
    assert result.metadata["symbol_extraction"]["symbols"] == [
        {
            "key": "store.First.checkout",
            "kind": "method",
            "phase": phase,
            "path": "store.py",
            "start_line": 2,
            "end_line": 3,
        }
        for phase in ("baseline", "candidate")
    ]
    result.metadata["caller"]["retained"] = False
    assert change.metadata["caller"]["retained"] is True


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("@old\ndef run():\n    pass\n", "@new\ndef run():\n    pass\n", ["sample.run"]),
        (
            "class Thing:\n    VALUE = 1\n    def run(self):\n        pass\n",
            "class Thing:\n    VALUE = 2\n    def run(self):\n        pass\n",
            ["sample.Thing"],
        ),
        (
            "VALUE = 1\ndef run():\n    pass\n",
            "VALUE = 2\ndef run():\n    pass\n",
            ["sample.<module>"],
        ),
        (
            "def outer():\n    async def inner():\n        return 1\n    return inner\n",
            "def outer():\n    async def inner():\n        return 2\n    return inner\n",
            ["sample.outer.inner"],
        ),
        (
            "def run():\n    removed = 1\n    return 2\n",
            "def run():\n    return 2\n",
            ["sample.run"],
        ),
        (
            "def removed():\n    pass\n\ndef kept():\n    pass\n",
            "\ndef kept():\n    pass\n",
            ["sample.removed"],
        ),
        (
            "def kept():\n    pass\n",
            "def added():\n    pass\n\ndef kept():\n    pass\n",
            ["sample.<module>", "sample.added"],
        ),
    ],
)
def test_changed_lines_select_only_containing_scope(
    git_repository: Path, before: str, after: str, expected: list[str]
) -> None:
    source = git_repository / "sample.py"
    source.write_text(before)
    base = _commit(git_repository)
    source.write_text(after)
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.changed_symbols == expected
    assert result.completeness == 1


def test_whole_file_add_delete_and_rename(git_repository: Path) -> None:
    empty = _commit(git_repository)
    source = git_repository / "original.py"
    source.write_text("VALUE = 1\nclass A:\n    def run(self):\n        pass\n")
    added = _commit(git_repository)
    source.rename(git_repository / "renamed.py")
    renamed = _commit(git_repository)
    (git_repository / "renamed.py").unlink()
    deleted = _commit(git_repository)

    addition = _extract(git_repository, empty, added)
    rename = _extract(git_repository, added, renamed)
    deletion = _extract(git_repository, renamed, deleted)

    assert addition.changed_symbols == ["original.<module>", "original.A", "original.A.run"]
    assert deletion.changed_symbols == ["renamed.<module>", "renamed.A", "renamed.A.run"]
    assert rename.changed_symbols == sorted(addition.changed_symbols + deletion.changed_symbols)
    assert rename.renamed_files == {"original.py": "renamed.py"}
    assert {s["phase"] for s in addition.metadata["symbol_extraction"]["symbols"]} == {"candidate"}
    assert {s["phase"] for s in deletion.metadata["symbol_extraction"]["symbols"]} == {"baseline"}


def test_rename_with_edit_preserves_old_and_new_identities(git_repository: Path) -> None:
    source = git_repository / "before.py"
    source.write_text("def old():\n    return 1\n" + "# unchanged\n" * 30)
    base = _commit(git_repository)
    source.rename(git_repository / "after.py")
    (git_repository / "after.py").write_text("def new():\n    return 2\n" + "# unchanged\n" * 30)
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.renamed_files == {"before.py": "after.py"}
    assert "before.old" in result.changed_symbols
    assert "after.new" in result.changed_symbols


def test_duplicate_declarations_and_dotted_paths_have_distinct_keys(git_repository: Path) -> None:
    base = _commit(git_repository)
    (git_repository / "a").mkdir()
    for path in ("a.b.py", "a/b.py"):
        (git_repository / path).write_text("def same():\n    pass\ndef same():\n    pass\n")
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.changed_symbols == ["a%2Eb.same", "a%2Eb.same#2", "a.b.same", "a.b.same#2"]


@pytest.mark.parametrize("name", ["odd [*]? name\t\n.py", ":(glob)*.py", "--source.py"])
def test_source_paths_are_literal(git_repository: Path, name: str) -> None:
    base = _commit(git_repository)
    (git_repository / name).write_text("def found():\n    pass\n")
    result = _extract(git_repository, base, _commit(git_repository))

    assert len(result.changed_symbols) == 1
    assert result.metadata["symbol_extraction"]["symbols"][0]["path"] == name
    assert result.completeness == 1


@pytest.mark.parametrize(
    ("contents", "code"),
    [
        (b"def broken(\n", "source_parse_error"),
        (b"# coding: nonexistent\n", "source_encoding_error"),
        (b"VALUE = '\xff'\n", "source_encoding_error"),
        (b"VALUE = 1\rOTHER = 2\r", "unsupported_line_endings"),
    ],
)
def test_bad_source_lowers_completeness_without_losing_valid_symbols(
    git_repository: Path, contents: bytes, code: str
) -> None:
    base = _commit(git_repository)
    (git_repository / "bad.py").write_bytes(contents)
    (git_repository / "good.py").write_text("def good():\n    pass\n")
    change = _change(git_repository, base, _commit(git_repository))
    change.completeness = 0.8
    result = extract_changed_symbols(repository_path=git_repository, change=change)

    assert result.changed_symbols == ["good.good"]
    assert result.completeness == 0.4
    assert result.metadata["symbol_extraction"]["diagnostics"] == [
        {"path": "bad.py", "phase": "candidate", "code": code}
    ]


def test_broken_candidate_preserves_baseline_symbols(git_repository: Path) -> None:
    path = git_repository / "source.py"
    path.write_text("def original():\n    pass\n")
    base = _commit(git_repository)
    path.write_text("def broken(\n")
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.changed_symbols == ["source.original"]
    assert result.completeness == 0.5


@pytest.mark.parametrize("to_symlink", [True, False])
def test_file_type_changes_are_visible_and_symlinks_are_not_followed(
    git_repository: Path, to_symlink: bool, tmp_path: Path
) -> None:
    external = tmp_path / "external.py"
    external.write_text("def secret():\n    pass\n")
    path = git_repository / "source.py"
    if to_symlink:
        path.write_text("def original():\n    pass\n")
    else:
        path.symlink_to(external)
    base = _commit(git_repository)
    path.unlink()
    if to_symlink:
        path.symlink_to(external)
    else:
        path.write_text("def original():\n    pass\n")
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.modified_files == ["source.py"]
    assert result.changed_symbols == ["source.original"]
    assert result.completeness == 0.5
    assert (
        result.metadata["symbol_extraction"]["diagnostics"][0]["code"] == "unsupported_object_type"
    )


def test_blob_limit_is_checked_before_reading_contents(
    git_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _commit(git_repository)
    (git_repository / "huge.py").write_bytes(b"#" * (symbols_module.MAX_BLOB_BYTES + 1))
    (git_repository / "small.py").write_text("def kept():\n    pass\n")
    change = _change(git_repository, base, _commit(git_repository))
    real_git = symbols_module._git
    blob_reads = []

    def recording_git(root: Path, arguments: list[str], operation: str) -> bytes:
        if arguments[:2] == ["cat-file", "blob"]:
            blob_reads.append(arguments[2])
        return real_git(root, arguments, operation)

    monkeypatch.setattr(symbols_module, "_git", recording_git)
    result = extract_changed_symbols(repository_path=git_repository, change=change)

    assert result.changed_symbols == ["small.kept"]
    assert result.completeness == 0.5
    assert len(blob_reads) == 1
    assert result.metadata["symbol_extraction"]["diagnostics"][0]["code"] == "blob_size_limit"


def test_snapshot_limit_is_deterministic(
    git_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _commit(git_repository)
    for name in ("a.py", "b.py", "c.py"):
        (git_repository / name).write_text("def found():\n    pass\n")
    candidate = _commit(git_repository)
    monkeypatch.setattr(symbols_module, "MAX_FILE_SNAPSHOTS", 2)
    result = _extract(git_repository, base, candidate)

    assert result.changed_symbols == ["a.found", "b.found"]
    assert result.completeness == pytest.approx(2 / 3)
    assert result.metadata["symbol_extraction"]["diagnostics"] == [
        {"path": "c.py", "phase": "candidate", "code": "snapshot_limit"}
    ]


def test_empty_diff_and_mode_only_changes(git_repository: Path) -> None:
    source = git_repository / "source.py"
    source.write_text("def unchanged():\n    pass\n")
    base = _commit(git_repository)
    _git(git_repository, "update-index", "--chmod=+x", "source.py")
    _git(git_repository, "commit", "-q", "-m", "mode only")
    candidate = _git(git_repository, "rev-parse", "HEAD")

    for result in (_extract(git_repository, base, base), _extract(git_repository, base, candidate)):
        assert result.changed_symbols == []
        assert result.completeness == 1


def test_supported_encodings_and_crlf(git_repository: Path) -> None:
    path = git_repository / "source.py"
    path.write_bytes(b"# coding: latin-1\r\ndef run():\r\n    return 'caf\xe9'\r\n")
    base = _commit(git_repository)
    path.write_bytes(path.read_bytes().replace(b"caf\xe9", b"th\xe9"))
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.changed_symbols == ["source.run"]
    assert result.completeness == 1


def test_parenthesized_decorator_includes_opening_line(git_repository: Path) -> None:
    source = git_repository / "source.py"
    source.write_text("@(\n    decorator\n)\ndef run():\n    pass\n")
    base = _commit(git_repository)
    source.write_text(source.read_text().replace("@(\n", "@(  # changed\n"))
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.changed_symbols == ["source.run"]
    assert all(s["start_line"] == 1 for s in result.metadata["symbol_extraction"]["symbols"])


def test_encoding_that_changes_line_count_is_incomplete(git_repository: Path) -> None:
    base = _commit(git_repository)
    (git_repository / "source.py").write_bytes(b"# coding: utf-7\ndef run():+AAo-    return 1\n")
    result = _extract(git_repository, base, _commit(git_repository))

    assert result.completeness == 0
    assert result.metadata["symbol_extraction"]["diagnostics"][0]["code"] == (
        "unsupported_encoding_line_mapping"
    )


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "x\0.py", "\udcff.py"])
def test_supplied_unsafe_paths_are_rejected(git_repository: Path, path: str) -> None:
    sha = _commit(git_repository)
    change = _change(git_repository, sha, sha)
    change.added_files = [path]
    with pytest.raises(GitExecutionError):
        extract_changed_symbols(repository_path=git_repository, change=change)


def test_extraction_requires_immutable_commits(git_repository: Path) -> None:
    sha = _commit(git_repository)
    change = _change(git_repository, sha, sha)
    change.base_commit_sha = "HEAD"
    with pytest.raises(InvalidCommitError):
        extract_changed_symbols(repository_path=git_repository, change=change)


def test_missing_source_is_incomplete(git_repository: Path) -> None:
    sha = _commit(git_repository)
    change = _change(git_repository, sha, sha)
    change.added_files = ["missing.py"]
    result = extract_changed_symbols(repository_path=git_repository, change=change)
    assert result.completeness == 0
    assert result.metadata["symbol_extraction"]["diagnostics"][0]["code"] == "missing_path"


def test_repository_source_and_external_diff_are_never_executed(
    git_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = git_repository / "source.py"
    path.write_text("raise RuntimeError('must not execute')\ndef run():\n    return 1\n")
    base = _commit(git_repository)
    path.write_text(path.read_text().replace("return 1", "return 2"))
    candidate = _commit(git_repository)
    _git(git_repository, "config", "diff.external", "/nonexistent-external-diff")
    _git(git_repository, "config", "diff.context", "100")
    _git(git_repository, "config", "diff.interHunkContext", "100")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/nonexistent-environment-diff")
    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=100")

    result = _extract(git_repository, base, candidate)

    assert result.changed_symbols == ["source.run"]
    assert result.completeness == 1


def test_broken_store_pipeline_matches_shared_symbol_identity(tmp_path: Path) -> None:
    fixture_root = Path(__file__).parents[3] / "fixtures" / "broken-store"
    repository = tmp_path / "fixture"
    subprocess.run(
        [sys.executable, str(fixture_root / "scripts" / "seed_fixture_repo.py"), str(repository)],
        check=True,
        capture_output=True,
    )
    result = _extract(repository, "good", "n-plus-one")
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "checkout_symbols.json").read_text()
    )

    assert result.changed_symbols == expected["changed_symbols"]
    assert result.metadata["symbol_extraction"]["symbols"] == expected["symbols"]
    assert result.completeness == 1
