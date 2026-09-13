from pathlib import Path

from lou.core.errors import (
    AnalysisNotImplementedError,
    GitExecutionError,
    InvalidCommitError,
    InvalidRepositoryError,
    LouError,
)


def test_base_errors_have_stable_safe_public_surfaces() -> None:
    errors = [
        (
            LouError(),
            "lou_error",
            "Lou could not complete the requested operation.",
        ),
        (
            AnalysisNotImplementedError(),
            "analysis_not_implemented",
            "Repository analysis is not available yet.",
        ),
    ]

    for error, code, public_message in errors:
        assert error.code == code
        assert error.public_message == public_message
        assert str(error) == public_message
        assert error.args == (public_message,)


def test_invalid_repository_error_keeps_diagnostics_out_of_public_message() -> None:
    path = Path("/private/server/repository")
    reason = "fatal: private repository diagnostic"
    error = InvalidRepositoryError(path, reason)

    assert error.code == "invalid_repository"
    assert error.path == path
    assert error.reason == reason
    assert str(error) == error.public_message
    assert str(path) not in error.public_message
    assert reason not in error.public_message


def test_invalid_commit_error_keeps_diagnostics_out_of_public_message() -> None:
    path = Path("/private/server/repository")
    revision = "private-feature-name"
    error = InvalidCommitError(path, revision, "candidate")

    assert error.code == "invalid_commit"
    assert error.path == path
    assert error.revision == revision
    assert error.role == "candidate"
    assert str(error) == error.public_message
    assert str(path) not in error.public_message
    assert revision not in error.public_message


def test_git_execution_error_keeps_diagnostics_out_of_public_message() -> None:
    stderr = "fatal: /private/server/repository could not be read"
    error = GitExecutionError("diff commits", 128, stderr)

    assert error.code == "git_execution_failed"
    assert error.operation == "diff commits"
    assert error.returncode == 128
    assert error.stderr == stderr
    assert error.timed_out is False
    assert error.timeout_seconds is None
    assert str(error) == error.public_message
    assert stderr not in error.public_message
