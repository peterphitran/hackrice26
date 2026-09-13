"""Tests for trusted, graph-backed adaptive validation planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from contracts import RepositoryContext
from lou.repository import (
    GraphSelectionCriterion,
    PlanningLimits,
    RegistryLimits,
    TrustedLoadConfiguration,
    WorkloadDefinition,
    WorkloadRegistry,
    fixture_commands,
    fixture_workload_registry,
    plan_validation_workloads,
    select_fixture_workloads,
)

FIXTURE = Path(__file__).parents[3] / "fixtures" / "broken-store"


def _checkout_context(*, completeness: float = 1.0) -> RepositoryContext:
    return RepositoryContext(
        repository_id="fixture",
        commit_sha="a" * 40,
        changed_symbols=["store.app.Store.checkout"],
        affected_tests=["tests/test_checkout.py::test_checkout_receipt_stays_correct"],
        affected_endpoints=["POST /checkout"],
        selected_workload_ids=["checkout-k6", "checkout-pytest"],
        selection_reasons={
            "checkout-pytest": "Reached checkout test via TESTED_BY.",
            "checkout-k6": "Reached load scenario via VALIDATED_BY.",
            "POST /checkout": "Reached endpoint via SERVES_ENDPOINT.",
        },
        unresolved_relationships=([] if completeness == 1 else ["unresolved_call: dynamic"]),
        completeness=completeness,
    )


def _pytest_definition(
    workload_id: str,
    criterion: GraphSelectionCriterion,
    *,
    priority: int = 100,
    cost: float = 10,
    fallback: bool = False,
    path: str = "tests/test_checkout.py",
    command: tuple[str, ...] = ("python", "-m", "pytest"),
) -> WorkloadDefinition:
    return WorkloadDefinition(
        workload_id=workload_id,
        workload_type="pytest",
        definition_path=path,
        priority=priority,
        estimated_cost_seconds=cost,
        criteria=(criterion,),
        fallback_eligible=fallback,
        default_reason=f"Default reason for {workload_id}.",
        trusted_command=command,
    )


def test_checkout_context_produces_exact_ordered_plan_with_graph_reasons() -> None:
    context = _checkout_context()

    first = plan_validation_workloads(context, fixture_workload_registry())
    second = plan_validation_workloads(context, fixture_workload_registry())

    assert first == second
    assert first.payload() == second.payload()
    assert [item.workload_id for item in first.selected] == [
        "checkout-pytest",
        "checkout-k6",
    ]
    assert [item.workload_type for item in first.selected] == ["pytest", "k6"]
    assert [item.reason for item in first.selected] == [
        "Reached checkout test via TESTED_BY.",
        "Reached load scenario via VALIDATED_BY.",
    ]
    assert first.omitted == ()
    assert first.state == "planned"
    assert first.completeness == first.confidence == 1
    assert first.budget.selected_workloads == 2
    assert first.budget.estimated_cost_seconds == 150
    assert select_fixture_workloads(context) == first.selected
    assert all(item.reason and item.confidence == 1.0 for item in first.selected)
    assert all((FIXTURE / item.definition_path).is_file() for item in first.selected)
    assert fixture_commands()["checkout-pytest"] == (
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_checkout.py",
    )


def test_matching_graph_evidence_selects_registered_check_and_skips_unrelated() -> None:
    matching = _pytest_definition(
        "orders-pytest",
        GraphSelectionCriterion("affected_symbols", "orders.service.checkout"),
        priority=10,
    )
    unrelated = _pytest_definition(
        "billing-pytest",
        GraphSelectionCriterion("affected_symbols", "billing.invoice.charge"),
        priority=1,
    )
    context = RepositoryContext(
        repository_id="orders",
        commit_sha="b" * 40,
        affected_symbols=["orders.service.checkout"],
        selection_reasons={
            "orders.service.checkout": "Reached FUNCTION via CALLS from changed checkout."
        },
        completeness=1,
    )

    plan = plan_validation_workloads(
        context, WorkloadRegistry("test-registry", (unrelated, matching))
    )

    assert [item.workload_id for item in plan.selected] == ["orders-pytest"]
    assert plan.selected[0].reason == context.selection_reasons["orders.service.checkout"]
    assert plan.selected[0].metadata["graph_evidence_source"] == "affected_symbols"
    assert plan.omitted == (
        plan.omitted[0].__class__(
            "billing-pytest",
            "node_evidence",
            "No declared graph-selection criterion matched complete graph evidence.",
        ),
    )


def test_graph_selected_registered_id_is_strong_evidence() -> None:
    entry = _pytest_definition(
        "explicit-check",
        GraphSelectionCriterion("affected_symbols", "unrelated.symbol"),
    )
    context = RepositoryContext(
        repository_id="explicit",
        commit_sha="f" * 40,
        selected_workload_ids=["explicit-check"],
        selection_reasons={"explicit-check": "Graph selected through VALIDATED_BY."},
        completeness=1,
    )

    plan = plan_validation_workloads(context, WorkloadRegistry("explicit-v1", (entry,)))

    assert [item.workload_id for item in plan.selected] == ["explicit-check"]
    assert plan.selected[0].reason == "Graph selected through VALIDATED_BY."


def test_priority_then_type_then_id_order_is_deterministic() -> None:
    criterion = GraphSelectionCriterion("changed_symbols", "service.changed")
    entries = (
        WorkloadDefinition(
            "load",
            "k6",
            "loadtests/check.js",
            20,
            10,
            (criterion,),
            False,
            "load",
            trusted_load=TrustedLoadConfiguration(),
        ),
        _pytest_definition("z-test", criterion, priority=20),
        _pytest_definition("a-test", criterion, priority=20),
        _pytest_definition("first", criterion, priority=1),
    )
    context = RepositoryContext(
        repository_id="order",
        commit_sha="c" * 40,
        changed_symbols=["service.changed"],
        completeness=1,
    )

    selected = plan_validation_workloads(context, WorkloadRegistry("order-v1", entries)).selected

    assert [item.workload_id for item in selected] == ["first", "a-test", "z-test", "load"]


def test_registry_rejects_duplicate_unsupported_unsafe_and_malformed_entries() -> None:
    criterion = GraphSelectionCriterion("changed_symbols", "service.changed")
    valid = _pytest_definition("valid", criterion)
    with pytest.raises(ValueError, match="duplicate workload IDs"):
        WorkloadRegistry("duplicate", (valid, valid))
    with pytest.raises(ValueError, match="unsupported workload type"):
        WorkloadDefinition(
            "unsafe",
            cast(Any, "shell"),
            "tests/check.py",
            1,
            1,
            (criterion,),
            False,
            "unsafe",
            trusted_command=("echo",),
        )
    for path in ("../test.py", "/tmp/test.py", "tests\\test.py", "a//test.py", "C:/test.py"):
        with pytest.raises(ValueError, match="definition_path"):
            _pytest_definition("unsafe-path", criterion, path=path)
    with pytest.raises(ValueError, match="non-empty tuple"):
        _pytest_definition("empty-command", criterion, command=cast(Any, []))
    with pytest.raises(ValueError, match="printable"):
        _pytest_definition("bad-command", criterion, command=("python", "bad\nargument"))
    with pytest.raises(ValueError, match="graph-selection criteria"):
        WorkloadDefinition(
            "mutable-criteria",
            "pytest",
            "tests/check.py",
            1,
            1,
            cast(Any, [criterion]),
            False,
            "reason",
            trusted_command=("pytest",),
        )


def test_registry_resource_limits_reject_oversized_entries() -> None:
    criterion = GraphSelectionCriterion("changed_symbols", "service.changed")
    entries = (
        _pytest_definition("one", criterion),
        _pytest_definition("two", criterion),
    )
    with pytest.raises(ValueError, match="entry limit"):
        WorkloadRegistry("small", entries, RegistryLimits(max_entries=1))
    with pytest.raises(ValueError, match="command argument limit"):
        WorkloadRegistry(
            "small",
            (entries[0],),
            RegistryLimits(max_command_arguments=1),
        )
    with pytest.raises(ValueError, match="estimated cost limit"):
        WorkloadRegistry(
            "small",
            (entries[0],),
            RegistryLimits(max_entry_cost_seconds=1),
        )
    with pytest.raises(ValueError, match="estimated workload cost"):
        _pytest_definition("nan-cost", criterion, cost=float("nan"))


def test_selection_count_and_total_cost_budgets_record_each_skip() -> None:
    criterion = GraphSelectionCriterion("changed_symbols", "service.changed")
    entries = tuple(
        _pytest_definition(identifier, criterion, priority=priority, cost=cost)
        for identifier, priority, cost in (
            ("first", 1, 5),
            ("second", 2, 6),
            ("third", 3, 7),
        )
    )
    registry = WorkloadRegistry("budgets", entries)
    context = RepositoryContext(
        repository_id="budget",
        commit_sha="d" * 40,
        changed_symbols=["service.changed"],
        completeness=1,
    )

    count = plan_validation_workloads(
        context, registry, limits=PlanningLimits(max_selected_workloads=1)
    )
    cost = plan_validation_workloads(
        context,
        registry,
        limits=PlanningLimits(max_total_estimated_cost_seconds=10),
    )

    assert [item.workload_id for item in count.selected] == ["first"]
    assert [(item.workload_id, item.category) for item in count.omitted] == [
        ("second", "budget"),
        ("third", "budget"),
    ]
    assert count.budget.selected_workloads == 1
    assert count.budget.estimated_cost_seconds == 5
    assert [item.workload_id for item in cost.selected] == ["first"]
    assert all(item.category == "budget" and item.reason for item in cost.omitted)


def test_complete_empty_incomplete_empty_and_configured_fallback_are_distinct() -> None:
    registry = fixture_workload_registry()
    complete = RepositoryContext(repository_id="empty", commit_sha="e" * 40, completeness=1)
    incomplete = complete.model_copy(
        update={"completeness": 0.4, "unresolved_relationships": ["traversal_node_limit"]}
    )

    complete_plan = plan_validation_workloads(complete, registry)
    incomplete_plan = plan_validation_workloads(incomplete, registry)
    fallback_plan = plan_validation_workloads(
        incomplete, registry, fallback_workload_ids=("checkout-pytest",)
    )

    assert complete_plan.selected == ()
    assert complete_plan.state == "complete_no_applicable"
    assert complete_plan.completeness == complete_plan.confidence == 1
    assert incomplete_plan.selected == ()
    assert incomplete_plan.state == "incomplete_no_fallback"
    assert incomplete_plan.confidence == 0
    assert fallback_plan.state == "incomplete_with_fallback"
    assert [item.workload_id for item in fallback_plan.selected] == ["checkout-pytest"]
    assert fallback_plan.selected[0].confidence == pytest.approx(0.2)
    assert "fallback" in fallback_plan.selected[0].reason
    assert fallback_plan.unresolved_graph_information == ("traversal_node_limit",)


def test_duplicates_are_suppressed_and_all_results_have_reasons() -> None:
    context = _checkout_context().model_copy(
        update={"selected_workload_ids": ["checkout-pytest", "checkout-pytest"]}
    )

    plan = plan_validation_workloads(context, fixture_workload_registry())

    assert [item.workload_id for item in plan.selected] == ["checkout-pytest", "checkout-k6"]
    assert any(item.category == "duplicate" for item in plan.omitted)
    assert all(item.reason for item in plan.selected)
    assert all(item.reason for item in plan.omitted)


def test_planner_has_no_command_or_model_text_input() -> None:
    context = _checkout_context()
    with pytest.raises(TypeError):
        plan_validation_workloads(
            context,
            fixture_workload_registry(),
            commands=("sh", "-c", "model output"),  # type: ignore[call-arg]
        )
    plan = plan_validation_workloads(context, fixture_workload_registry())
    assert all("command" not in item.metadata for item in plan.selected)
