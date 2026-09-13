"""Run INT-002 from a disposable clone of the current committed branch."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parents[2]
_MARKER = "__LOU_CLEAN_CLONE_RESULT__"


def _branch(source: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "branch", "--show-current"], text=True
    ).strip()


def rehearse(source: Path, ref: str | None = None) -> int:
    """Clone committed files only, then execute the normal local controller."""

    source = source.resolve(strict=True)
    chosen_ref = ref or _branch(source)
    with TemporaryDirectory(prefix="lou-clean-clone-") as temporary:
        clone = Path(temporary) / "repository"
        subprocess.run(
            ["git", "clone", "--no-local", "--branch", chosen_ref, str(source), str(clone)],
            check=True,
        )
        backend = clone / "backend"
        environment = os.environ | {
            "PYTHONPATH": str(backend),
            "LOU_INT002_POSTGRES_PORT": "55434",
        }
        completed = subprocess.run(
            [sys.executable, "-m", "lou.verification.int002"],
            cwd=backend,
            env=environment,
            check=False,
        )
    status = "succeeded" if completed.returncode == 0 else "failed"
    print(f"{_MARKER} ref={chosen_ref} status={status}")
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse the local demo from a clean clone.")
    parser.add_argument("--source", type=Path, default=_ROOT)
    parser.add_argument("--ref", help="Committed local branch or revision to clone.")
    arguments = parser.parse_args()
    return rehearse(arguments.source, arguments.ref)


if __name__ == "__main__":
    raise SystemExit(main())
