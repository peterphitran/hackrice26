"""Disposable detached Git worktrees for immutable verification phases."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


class WorktreeError(RuntimeError):
    """Raised when Lou cannot safely create or remove a verification worktree."""


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@contextmanager
def disposable_worktree(repository: Path, commit_sha: str) -> Iterator[Path]:
    """Yield an exact detached commit and remove it regardless of phase outcome.

    The caller's checkout is never switched or modified.  The strict SHA requirement
    intentionally matches the first local verification slice's preflight contract.
    """

    if _SHA1.fullmatch(commit_sha) is None:
        raise ValueError("commit_sha must be an exact 40-character SHA-1")
    source = repository.resolve(strict=True)
    with TemporaryDirectory(prefix="lou-phase-") as temporary:
        workspace = Path(temporary) / "worktree"
        added = _git(source, "worktree", "add", "--detach", str(workspace), commit_sha)
        if added.returncode != 0:
            detail = added.stderr.strip() or "Git refused the requested worktree"
            raise WorktreeError(f"could not create verification worktree: {detail}")
        try:
            yield workspace
        finally:
            removed = _git(source, "worktree", "remove", "--force", str(workspace))
            _git(source, "worktree", "prune")
            if removed.returncode != 0:
                detail = removed.stderr.strip() or "Git refused to remove the worktree"
                raise WorktreeError(f"could not remove verification worktree: {detail}")
