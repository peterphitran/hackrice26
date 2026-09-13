from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[1]
COMMIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True, env=COMMIT_ENV)


def seed(target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")

    ignored = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(FIXTURE_ROOT / "template", target, ignore=ignored)
    shutil.copytree(FIXTURE_ROOT / "tests", target / "tests", ignore=ignored)
    shutil.copytree(FIXTURE_ROOT / "loadtests", target / "loadtests", ignore=ignored)

    git(target, "init", "--initial-branch=main")
    git(target, "config", "user.name", "Lou Fixture")
    git(target, "config", "user.email", "fixture@lou.local")
    git(target, "config", "core.autocrlf", "false")
    git(target, "add", ".")
    git(target, "commit", "-m", "good checkout")
    git(target, "tag", "good")

    git(target, "apply", str(FIXTURE_ROOT / "variants" / "n_plus_one.patch"))
    git(target, "add", "store/app.py")
    git(target, "commit", "-m", "introduce N+1 checkout query")
    git(target, "tag", "n-plus-one")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the broken-store benchmark repository.")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    seed(args.target.resolve())


if __name__ == "__main__":
    main()
