import subprocess
from pathlib import Path

import pytest


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
