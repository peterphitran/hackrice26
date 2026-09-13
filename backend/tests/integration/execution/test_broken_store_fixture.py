from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "broken-store"
SEED_SCRIPT = FIXTURE_ROOT / "scripts" / "seed_fixture_repo.py"


def test_seed_creates_stable_good_and_n_plus_one_refs(tmp_path: Path) -> None:
    module_spec = importlib.util.spec_from_file_location("seed_fixture_repo", SEED_SCRIPT)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    repository = tmp_path / "broken-store"

    module.seed(repository)

    refs = subprocess.run(
        ["git", "-C", str(repository), "tag", "--list"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert refs == ["good", "n-plus-one"]

    expected_query_counts = {"good": "2", "n-plus-one": "51"}
    for ref in refs:
        subprocess.run(["git", "-C", str(repository), "switch", "--detach", ref], check=True)
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repository,
            check=True,
            env={**os.environ, "EXPECTED_QUERY_COUNT": expected_query_counts[ref]},
        )

    second_repository = tmp_path / "broken-store-second"
    module.seed(second_repository)
    for ref in refs:
        first_sha = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        second_sha = subprocess.run(
            ["git", "-C", str(second_repository), "rev-parse", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert first_sha == second_sha
