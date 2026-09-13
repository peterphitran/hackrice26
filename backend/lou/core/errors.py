"""Typed errors used at application boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Literal


class LouError(Exception):
    """Base exception for expected Lou failures."""

    code = "lou_error"
    public_message = "Lou could not complete the requested operation."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class AnalysisNotImplementedError(LouError):
    """Raised until the analysis application service is integrated."""

    code = "analysis_not_implemented"
    public_message = "Repository analysis is not available yet."


class InvalidRepositoryError(LouError):
    """Raised when a path does not identify a usable Git working tree."""

    code = "invalid_repository"
    public_message = "The repository could not be accessed."

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__()


class InvalidCommitError(LouError):
    """Raised when a requested revision does not resolve to a commit."""

    code = "invalid_commit"

    def __init__(
        self,
        path: Path,
        revision: str,
        role: Literal["base", "candidate"],
    ) -> None:
        self.path = path
        self.revision = revision
        self.role = role
        self.public_message = f"The {role} commit is invalid or unavailable."
        super().__init__()


class GitExecutionError(LouError):
    """Raised when Git cannot execute or returns unusable output."""

    code = "git_execution_failed"
    public_message = "Git could not complete the repository operation."

    def __init__(
        self,
        operation: str,
        returncode: int | None,
        stderr: str,
        *,
        timed_out: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        self.operation = operation
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out
        self.timeout_seconds = timeout_seconds
        super().__init__()
