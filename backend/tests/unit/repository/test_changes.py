from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from contracts import RepositoryChange
from lou.core.errors import GitExecutionError, InvalidCommitError, InvalidRepositoryError
from lou.repository import changes as changes_module
from lou.repository import parse_repository_changes


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _parse(
    repository: Path,
    base_revision: str,
    candidate_revision: str,
) -> RepositoryChange:
    return parse_repository_changes(
        repository_id="repo-1",
        repository_path=repository,
        base_revision=base_revision,
        candidate_revision=candidate_revision,
    )


def test_empty_diff(git_repository: Path) -> None:
    commit = _commit(git_repository, "initial")

    change = _parse(git_repository, commit, commit)

    assert change.added_files == []
    assert change.modified_files == []
    assert change.deleted_files == []
    assert change.renamed_files == {}
    assert change.completeness == 1.0


def test_added_python_file(git_repository: Path) -> None:
    base = _commit(git_repository, "base")
    (git_repository / "new file.py").write_text("VALUE = 1\n")
    candidate = _commit(git_repository, "add Python file")

    change = _parse(git_repository, base, candidate)

    assert change.added_files == ["new file.py"]
    assert change.changed_symbols == []


def test_modified_python_file_ignores_uncommitted_worktree(
    git_repository: Path,
) -> None:
    source = git_repository / "module.py"
    source.write_text("VALUE = 1\n")
    base = _commit(git_repository, "base")
    source.write_text("VALUE = 2\n")
    candidate = _commit(git_repository, "modify")
    source.write_text("VALUE = 3\n")
    (git_repository / "untracked.py").write_text("IGNORED = True\n")

    change = _parse(git_repository, base, candidate)

    assert change.modified_files == ["module.py"]
    assert change.added_files == []


def test_deleted_python_file(git_repository: Path) -> None:
    source = git_repository / "deleted.py"
    source.write_text("VALUE = 1\n")
    base = _commit(git_repository, "base")
    source.unlink()
    candidate = _commit(git_repository, "delete")

    change = _parse(git_repository, base, candidate)

    assert change.deleted_files == ["deleted.py"]


def test_renamed_python_file(git_repository: Path) -> None:
    source = git_repository / "old name.py"
    source.write_text("VALUE = 1\n")
    base = _commit(git_repository, "base")
    _git(git_repository, "mv", "old name.py", "new name.py")
    candidate = _commit(git_repository, "rename")

    change = _parse(git_repository, base, candidate)

    assert change.renamed_files == {"old name.py": "new name.py"}
    assert change.added_files == []
    assert change.deleted_files == []


@pytest.mark.parametrize(
    ("old_name", "new_name", "expected_added", "expected_deleted"),
    [
        ("module.py", "module.txt", [], ["module.py"]),
        ("module.txt", "module.py", ["module.py"], []),
    ],
)
def test_rename_crossing_python_scope_is_an_addition_or_deletion(
    git_repository: Path,
    old_name: str,
    new_name: str,
    expected_added: list[str],
    expected_deleted: list[str],
) -> None:
    (git_repository / old_name).write_text("VALUE = 1\n")
    base = _commit(git_repository, "base")
    _git(git_repository, "mv", old_name, new_name)
    candidate = _commit(git_repository, "rename across scope")

    change = _parse(git_repository, base, candidate)

    assert change.added_files == expected_added
    assert change.deleted_files == expected_deleted
    assert change.renamed_files == {}


def test_multiple_change_types_are_sorted_and_non_python_files_are_ignored(
    git_repository: Path,
) -> None:
    for name in ["z_modified.py", "b_deleted.py", "z_old.py", "a_old.py", "notes.txt"]:
        (git_repository / name).write_text(f"# {name}\n")
    base = _commit(git_repository, "base")

    (git_repository / "z_modified.py").write_text("# changed\n")
    (git_repository / "b_deleted.py").unlink()
    _git(git_repository, "mv", "z_old.py", "z_new.py")
    _git(git_repository, "mv", "a_old.py", "a_new.py")
    (git_repository / "z_added.py").write_text("# added\n")
    (git_repository / "a_added.py").write_text("# added\n")
    (git_repository / "notes.txt").write_text("changed but ignored\n")
    (git_repository / "other.md").write_text("ignored\n")
    candidate = _commit(git_repository, "mixed changes")

    change = _parse(git_repository, base, candidate)

    assert change.added_files == ["a_added.py", "z_added.py"]
    assert change.modified_files == ["z_modified.py"]
    assert change.deleted_files == ["b_deleted.py"]
    assert list(change.renamed_files.items()) == [
        ("a_old.py", "a_new.py"),
        ("z_old.py", "z_new.py"),
    ]


def test_nested_input_path_still_returns_repository_relative_posix_paths(
    git_repository: Path,
) -> None:
    base = _commit(git_repository, "base")
    nested = git_repository / "nested" / "package"
    nested.mkdir(parents=True)
    (nested / "module.py").write_text("VALUE = 1\n")
    candidate = _commit(git_repository, "nested file")

    change = _parse(nested, base, candidate)

    assert change.added_files == ["nested/package/module.py"]


def test_repository_core_worktree_cannot_redirect_analysis(
    git_repository: Path,
    tmp_path: Path,
) -> None:
    _commit(git_repository, "base")
    decoy_repository = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy_repository)], check=True)
    _git(git_repository, "config", "core.worktree", str(decoy_repository))

    with pytest.raises(InvalidRepositoryError) as error:
        _parse(git_repository, "HEAD", "HEAD")

    assert error.value.path == git_repository
    assert error.value.reason == "Git working-tree root does not contain the supplied path"


def test_revisions_resolve_to_full_commit_ids_and_serialize(git_repository: Path) -> None:
    base = _commit(git_repository, "base")
    _git(git_repository, "tag", "base-tag", base)
    (git_repository / "module.py").write_text("VALUE = 1\n")
    candidate = _commit(git_repository, "candidate")

    change = _parse(git_repository, "base-tag", "HEAD")
    serialized = change.model_dump(mode="json")
    round_tripped = RepositoryChange.model_validate_json(change.model_dump_json())

    assert change.base_commit_sha == base
    assert change.candidate_commit_sha == candidate
    assert serialized["repository_id"] == "repo-1"
    assert serialized["changed_symbols"] == []
    assert round_tripped == change
    assert change.metadata == {
        "extractor": "git",
        "original_base_revision": "base-tag",
        "original_candidate_revision": "HEAD",
        "rename_similarity_threshold": 50,
    }


def test_git_replace_refs_do_not_change_exact_commit_comparison(git_repository: Path) -> None:
    base = _commit(git_repository, "base")
    (git_repository / "module.py").write_text("VALUE = 1\n")
    candidate = _commit(git_repository, "candidate")
    _git(git_repository, "replace", candidate, base)

    change = _parse(git_repository, base, candidate)

    assert change.added_files == ["module.py"]


def test_missing_repository_path_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing"

    with pytest.raises(InvalidRepositoryError) as error:
        _parse(path, "HEAD", "HEAD")

    assert error.value.path == path
    assert error.value.reason == "path does not exist"


def test_file_repository_path_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("not a repository")

    with pytest.raises(InvalidRepositoryError) as error:
        _parse(path, "HEAD", "HEAD")

    assert error.value.path == path
    assert error.value.reason == "path is not a directory"


def test_non_git_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidRepositoryError) as error:
        _parse(tmp_path, "HEAD", "HEAD")

    assert error.value.path == tmp_path
    assert error.value.reason


def test_bare_repository_is_rejected(tmp_path: Path) -> None:
    bare_repository = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare_repository)], check=True)

    with pytest.raises(InvalidRepositoryError):
        _parse(bare_repository, "HEAD", "HEAD")


def test_invalid_base_revision_is_rejected(git_repository: Path) -> None:
    candidate = _commit(git_repository, "candidate")

    with pytest.raises(InvalidCommitError) as error:
        _parse(git_repository, "missing-base", candidate)

    assert error.value.role == "base"
    assert error.value.revision == "missing-base"


def test_invalid_candidate_revision_is_rejected(git_repository: Path) -> None:
    base = _commit(git_repository, "base")

    with pytest.raises(InvalidCommitError) as error:
        _parse(git_repository, base, "missing-candidate")

    assert error.value.role == "candidate"


def test_option_like_revision_is_not_interpreted_as_git_option(git_repository: Path) -> None:
    candidate = _commit(git_repository, "candidate")

    with pytest.raises(InvalidCommitError) as error:
        _parse(git_repository, "--help", candidate)

    assert error.value.revision == "--help"


def test_revision_containing_nul_is_rejected_as_an_invalid_commit(
    git_repository: Path,
) -> None:
    candidate = _commit(git_repository, "candidate")

    with pytest.raises(InvalidCommitError) as error:
        _parse(git_repository, "invalid\0revision", candidate)

    assert error.value.role == "base"
    assert error.value.revision == "invalid\0revision"


def test_inherited_git_repository_environment_is_ignored(
    git_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _commit(git_repository, "base")
    (git_repository / "module.py").write_text("VALUE = 1\n")
    candidate = _commit(git_repository, "candidate")
    decoy_repository = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy_repository)], check=True)
    monkeypatch.setenv("GIT_DIR", str(decoy_repository / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy_repository))

    change = _parse(git_repository, base, candidate)

    assert change.added_files == ["module.py"]


def test_git_execution_sanitizes_only_repository_selection_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in changes_module._GIT_REPOSITORY_ENVIRONMENT_VARIABLES:
        monkeypatch.setenv(variable, f"conflicting-{variable.lower()}")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/conflicting/worktree")
    monkeypatch.setenv("PATH", "/expected/bin")
    captured_environment: dict[str, str] = {}

    def capture_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured_environment.update(kwargs["env"])
        assert kwargs["timeout"] == changes_module._GIT_TIMEOUT_SECONDS
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(changes_module.subprocess, "run", capture_run)

    changes_module._execute_git(tmp_path, ["status"], operation="inspect repository")

    assert all(
        variable not in captured_environment
        for variable in changes_module._GIT_REPOSITORY_ENVIRONMENT_VARIABLES
    )
    assert "GIT_CONFIG_KEY_0" not in captured_environment
    assert "GIT_CONFIG_VALUE_0" not in captured_environment
    assert captured_environment["PATH"] == "/expected/bin"
    assert captured_environment["LC_ALL"] == "C"
    assert captured_environment["LANG"] == "C"


def test_timed_out_git_command_is_a_git_execution_error(
    git_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["timeout"] == changes_module._GIT_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(
            cmd=["git", "-C", "/private/server/repository", "status"],
            timeout=changes_module._GIT_TIMEOUT_SECONDS,
            stderr=b"fatal: private repository diagnostic",
        )

    monkeypatch.setattr(changes_module.subprocess, "run", time_out)

    with pytest.raises(GitExecutionError) as error:
        _parse(git_repository, "HEAD", "HEAD")

    assert error.value.operation == "repository validation"
    assert error.value.returncode is None
    assert error.value.stderr == "fatal: private repository diagnostic"
    assert error.value.timed_out is True
    assert error.value.timeout_seconds == 30.0
    assert str(error.value) == error.value.public_message
    assert "/private/server/repository" not in str(error.value)
    assert "private repository diagnostic" not in str(error.value)


def test_nonzero_diff_exit_is_a_git_execution_error(
    git_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = _commit(git_repository, "commit")
    real_execute = changes_module._execute_git

    def fail_diff(
        repository_path: Path,
        arguments: Sequence[str],
        *,
        operation: str,
    ) -> subprocess.CompletedProcess[bytes]:
        if "diff" in arguments:
            return subprocess.CompletedProcess(arguments, 128, b"", b"simulated failure")
        return real_execute(repository_path, arguments, operation=operation)

    monkeypatch.setattr(changes_module, "_execute_git", fail_diff)

    with pytest.raises(GitExecutionError) as error:
        _parse(git_repository, commit, commit)

    assert error.value.operation == "diff commits"
    assert error.value.returncode == 128
    assert error.value.stderr == "simulated failure"


def test_git_spawn_failure_is_a_git_execution_error(
    git_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_spawn(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("git unavailable")

    monkeypatch.setattr(changes_module.subprocess, "run", fail_spawn)

    with pytest.raises(GitExecutionError) as error:
        _parse(git_repository, "HEAD", "HEAD")

    assert error.value.returncode is None
    assert error.value.stderr == "git unavailable"
    assert "git unavailable" not in str(error.value)


def test_repository_errors_expose_safe_public_metadata(git_repository: Path) -> None:
    candidate = _commit(git_repository, "candidate")

    with pytest.raises(InvalidCommitError) as error:
        _parse(git_repository, "secret-revision", candidate)

    assert error.value.code == "invalid_commit"
    assert error.value.public_message == "The base commit is invalid or unavailable."
    assert str(error.value) == error.value.public_message
    assert "secret-revision" not in error.value.public_message
    assert str(git_repository) not in error.value.public_message


def test_git_errors_keep_diagnostics_separate_from_public_message() -> None:
    error = GitExecutionError("diff commits", 128, "sensitive internal diagnostic")

    assert error.code == "git_execution_failed"
    assert error.public_message == "Git could not complete the repository operation."
    assert error.stderr == "sensitive internal diagnostic"
    assert str(error) == error.public_message
    assert "sensitive internal diagnostic" not in error.public_message
