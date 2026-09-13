"""Resolve graph-selected workload IDs against the checked-in fixture registry."""

from __future__ import annotations

from contracts import RepositoryContext, WorkloadSelection

REGISTRY_REVISION = "broken-store-v1"
_PYTEST_ID = "checkout-pytest"
_K6_ID = "checkout-k6"
_PYTEST_PATH = "tests/test_checkout.py"
_K6_PATH = "loadtests/checkout.js"
_PYTEST_COMMAND = (
    "python",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    _PYTEST_PATH,
)


def fixture_workloads() -> tuple[WorkloadSelection, ...]:
    """Return the only two workloads allowed in the first local slice."""

    return (
        WorkloadSelection(
            workload_id=_PYTEST_ID,
            workload_type="pytest",
            definition_path=_PYTEST_PATH,
            phase="candidate",
            reason="Fixture checkout correctness gate.",
            confidence=1.0,
            metadata={
                "registry_revision": REGISTRY_REVISION,
                "test_path": _PYTEST_PATH,
            },
        ),
        WorkloadSelection(
            workload_id=_K6_ID,
            workload_type="k6",
            definition_path=_K6_PATH,
            phase="candidate",
            reason="Fixture checkout query-count workload.",
            confidence=1.0,
            metadata={
                "registry_revision": REGISTRY_REVISION,
                "endpoint": "POST /checkout",
            },
        ),
    )


def fixture_commands() -> dict[str, tuple[str, ...]]:
    """Return trusted command arrays, never executable CLI input."""

    return {_PYTEST_ID: _PYTEST_COMMAND}


def select_fixture_workloads(
    context: RepositoryContext,
    *,
    fallback_workload_ids: tuple[str, ...] = (),
) -> tuple[WorkloadSelection, ...]:
    """Turn graph-selected IDs into ordered registry records with explanations."""

    selected: list[WorkloadSelection] = []
    available = {item.workload_id: item for item in fixture_workloads()}
    fallback = set(fallback_workload_ids)
    for workload in fixture_workloads():
        if workload.workload_id in context.selected_workload_ids:
            selected.append(
                workload.model_copy(
                    update={
                        "reason": (
                            context.selection_reasons.get(workload.workload_id) or workload.reason
                        )
                    }
                )
            )
        elif workload.workload_id in fallback:
            selected.append(
                workload.model_copy(
                    update={
                        "reason": (
                            "Configured fallback because repository graph extraction is incomplete."
                        ),
                        "confidence": 0.0,
                        "metadata": {**workload.metadata, "fallback": True},
                    }
                )
            )
    unknown = sorted(set(context.selected_workload_ids) - set(available))
    if unknown:
        raise ValueError(f"unknown workload IDs in repository context: {', '.join(unknown)}")
    return tuple(selected)
