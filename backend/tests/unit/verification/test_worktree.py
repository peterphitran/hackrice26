"""Tests for isolated baseline/candidate verification worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lou.verification.worktree import disposable_worktree


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Lou Tests"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "lou-tests@example.invalid"],
        check=True,
    )
    return repository


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, value: str) -> str:
    (repository / "module.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    _git(repository, "add", "module.py")
    _git(repository, "commit", "-qm", f"value {value}")
    return _git(repository, "rev-parse", "HEAD")


def test_worktree_uses_exact_commit_and_preserves_callers_checkout(git_repository: Path) -> None:
    base = _commit(git_repository, "1")
    candidate = _commit(git_repository, "2")
    original_head = _git(git_repository, "rev-parse", "HEAD")

    with disposable_worktree(git_repository, base) as workspace:
        assert _git(workspace, "rev-parse", "HEAD") == base
        assert (workspace / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert _git(git_repository, "rev-parse", "HEAD") == original_head == candidate

    listed = _git(git_repository, "worktree", "list", "--porcelain")
    assert str(workspace) not in listed


def test_worktree_is_removed_when_phase_raises(git_repository: Path) -> None:
    commit = _commit(git_repository, "1")

    with pytest.raises(RuntimeError, match="simulated phase failure"):
        with disposable_worktree(git_repository, commit) as workspace:
            assert workspace.is_dir()
            raise RuntimeError("simulated phase failure")

    listed = _git(git_repository, "worktree", "list", "--porcelain")
    assert str(workspace) not in listed


def test_worktree_rejects_noncanonical_commit(git_repository: Path) -> None:
    with pytest.raises(ValueError, match="40-character"):
        with disposable_worktree(git_repository, "HEAD"):
            pass
