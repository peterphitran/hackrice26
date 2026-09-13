"""Resolve traversal selections through the trusted fixture workload registry."""

from __future__ import annotations

from pathlib import Path

from contracts import RepositoryContext
from lou.repository import fixture_commands, select_fixture_workloads

FIXTURE = Path(__file__).parents[3] / "fixtures" / "broken-store"


def test_graph_ids_produce_ordered_registry_records_with_graph_reasons() -> None:
    context = RepositoryContext(
        repository_id="fixture",
        commit_sha="a" * 40,
        selected_workload_ids=["checkout-k6", "checkout-pytest"],
        selection_reasons={
            "checkout-pytest": "Reached checkout test via TESTED_BY.",
            "checkout-k6": "Reached load scenario via VALIDATED_BY.",
        },
        completeness=1.0,
    )

    first = select_fixture_workloads(context)
    second = select_fixture_workloads(context)

    assert first == second
    assert [item.workload_id for item in first] == ["checkout-pytest", "checkout-k6"]
    assert [item.workload_type for item in first] == ["pytest", "k6"]
    assert [item.reason for item in first] == [
        "Reached checkout test via TESTED_BY.",
        "Reached load scenario via VALIDATED_BY.",
    ]
    assert all(item.reason and item.confidence == 1.0 for item in first)
    assert all((FIXTURE / item.definition_path).is_file() for item in first)
    assert fixture_commands()["checkout-pytest"] == (
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_checkout.py",
    )


def test_incomplete_context_includes_configured_fallback() -> None:
    context = RepositoryContext(
        repository_id="fixture",
        commit_sha="a" * 40,
        selected_workload_ids=["checkout-pytest"],
        selection_reasons={"checkout-pytest": "Reached checkout test via TESTED_BY."},
        completeness=0.5,
    )

    records = select_fixture_workloads(context, fallback_workload_ids=("checkout-k6",))

    assert [item.workload_id for item in records] == ["checkout-pytest", "checkout-k6"]
    assert records[1].reason == (
        "Configured fallback because repository graph extraction is incomplete."
    )
    assert records[1].confidence == 0.0
    assert records[1].metadata["fallback"] is True
