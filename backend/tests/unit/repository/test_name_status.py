import pytest

from lou.core.errors import GitExecutionError
from lou.repository.changes import _parse_name_status_z


def test_empty_name_status_output() -> None:
    parsed = _parse_name_status_z(b"")

    assert parsed.added_files == ()
    assert parsed.modified_files == ()
    assert parsed.deleted_files == ()
    assert parsed.renamed_files == ()


def test_parses_mixed_records_and_sorts_paths() -> None:
    parsed = _parse_name_status_z(
        b"A\0z file.py\0M\0middle.py\0D\0old.py\0R100\0before.py\0after.py\0"
        b"A\0alpha.py\0A\0notes.txt\0"
    )

    assert parsed.added_files == ("alpha.py", "z file.py")
    assert parsed.modified_files == ("middle.py",)
    assert parsed.deleted_files == ("old.py",)
    assert parsed.renamed_files == (("before.py", "after.py"),)


def test_preserves_whitespace_in_paths() -> None:
    parsed = _parse_name_status_z(b"A\0folder/a file\twith\nlines.py\0")

    assert parsed.added_files == ("folder/a file\twith\nlines.py",)


def test_type_change_is_reported_as_modified() -> None:
    assert _parse_name_status_z(b"T\0changed.py\0").modified_files == ("changed.py",)


@pytest.mark.parametrize(
    ("output", "added", "deleted"),
    [
        (b"R100\0before.py\0after.txt\0", (), ("before.py",)),
        (b"R087\0before.txt\0after.py\0", ("after.py",), ()),
        (b"R100\0before.txt\0after.txt\0", (), ()),
    ],
)
def test_classifies_renames_crossing_python_scope(
    output: bytes,
    added: tuple[str, ...],
    deleted: tuple[str, ...],
) -> None:
    parsed = _parse_name_status_z(output)

    assert parsed.added_files == added
    assert parsed.deleted_files == deleted
    assert parsed.renamed_files == ()


@pytest.mark.parametrize(
    "output",
    [
        b"A\0missing-trailing-nul.py",
        b"A\0",
        b"A\0\0",
        b"R100\0only-one-path.py\0",
        b"R101\0old.py\0new.py\0",
        b"A\0../escape.py\0",
        b"A\0./not-normalized.py\0",
        b"A\0bad-utf8-\xff.py\0",
        b"\xff\0file.py\0",
    ],
)
def test_rejects_malformed_name_status_output(output: bytes) -> None:
    with pytest.raises(GitExecutionError):
        _parse_name_status_z(output)
