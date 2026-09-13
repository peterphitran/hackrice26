"""Trusted, deterministic adaptive-validation workload planning."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal, TypeAlias

from contracts import RepositoryContext, WorkloadSelection

REGISTRY_REVISION = "broken-store-v2"

WorkloadType: TypeAlias = Literal["pytest", "k6", "semgrep", "custom"]
CriterionSource: TypeAlias = Literal[
    "changed_symbols",
    "affected_symbols",
    "affected_tests",
    "affected_endpoints",
    "affected_data_dependencies",
    "selected_workload_ids",
    "selection_reasons",
]
CriterionMatch: TypeAlias = Literal["exact", "prefix", "contains"]
OmissionCategory: TypeAlias = Literal[
    "node_evidence",
    "budget",
    "duplicate",
    "incomplete_graph",
    "unsupported_type",
    "unregistered",
]
PlanState: TypeAlias = Literal[
    "planned",
    "complete_no_applicable",
    "incomplete_no_fallback",
    "incomplete_with_fallback",
    "budget_exhausted",
]

_WORKLOAD_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SUPPORTED_TYPES = frozenset({"pytest", "k6", "semgrep", "custom"})
_TYPE_ORDER: dict[str, int] = {"pytest": 0, "semgrep": 1, "custom": 2, "k6": 3}
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


@dataclass(frozen=True)
class RegistryLimits:
    """Bounds applied while accepting trusted checked-in definitions."""

    max_entries: int = 64
    max_criteria_per_entry: int = 32
    max_command_arguments: int = 32
    max_command_bytes: int = 4096
    max_definition_path_bytes: int = 512
    max_entry_cost_seconds: float = 3600

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("registry limits must be positive")


@dataclass(frozen=True)
class PlanningLimits:
    """Bounds for one finalized adaptive-validation plan."""

    max_selected_workloads: int = 8
    max_total_estimated_cost_seconds: float = 300

    def __post_init__(self) -> None:
        if isinstance(self.max_selected_workloads, bool) or self.max_selected_workloads <= 0:
            raise ValueError("max_selected_workloads must be positive")
        if (
            isinstance(self.max_total_estimated_cost_seconds, bool)
            or not math.isfinite(self.max_total_estimated_cost_seconds)
            or self.max_total_estimated_cost_seconds <= 0
        ):
            raise ValueError("max_total_estimated_cost_seconds must be positive")


@dataclass(frozen=True)
class GraphSelectionCriterion:
    """One trusted declaration of graph evidence relevant to a workload."""

    source: CriterionSource
    value: str
    match: CriterionMatch = "exact"

    def __post_init__(self) -> None:
        if self.source not in {
            "changed_symbols",
            "affected_symbols",
            "affected_tests",
            "affected_endpoints",
            "affected_data_dependencies",
            "selected_workload_ids",
            "selection_reasons",
        }:
            raise ValueError(f"unsupported graph criterion source: {self.source}")
        if self.match not in {"exact", "prefix", "contains"}:
            raise ValueError(f"unsupported graph criterion match: {self.match}")
        if not self.value.strip() or any(ord(character) < 32 for character in self.value):
            raise ValueError("graph criterion value must be non-empty printable text")


@dataclass(frozen=True)
class TrustedLoadConfiguration:
    """Registry-owned load-test settings; never sourced from repository text."""

    driver: Literal["k6"] = "k6"
    warmup_runs: int = 1
    repetitions: int = 5

    def __post_init__(self) -> None:
        if self.driver != "k6":
            raise ValueError("unsupported load-test driver")
        if self.warmup_runs != 1 or self.repetitions <= 0:
            raise ValueError("k6 configuration requires one warmup and positive repetitions")


@dataclass(frozen=True)
class WorkloadDefinition:
    """One local checked-in workload whose executable fields are registry-owned."""

    workload_id: str
    workload_type: WorkloadType
    definition_path: str
    priority: int
    estimated_cost_seconds: float
    criteria: tuple[GraphSelectionCriterion, ...]
    fallback_eligible: bool
    default_reason: str
    trusted_command: tuple[str, ...] | None = None
    trusted_load: TrustedLoadConfiguration | None = None

    def __post_init__(self) -> None:
        if _WORKLOAD_ID.fullmatch(self.workload_id) is None:
            raise ValueError("workload_id is invalid")
        if self.workload_type not in _SUPPORTED_TYPES:
            raise ValueError(f"unsupported workload type: {self.workload_type}")
        _validate_definition_path(self.definition_path)
        if isinstance(self.priority, bool) or self.priority < 0:
            raise ValueError("workload priority must not be negative")
        if (
            isinstance(self.estimated_cost_seconds, bool)
            or not math.isfinite(self.estimated_cost_seconds)
            or self.estimated_cost_seconds <= 0
        ):
            raise ValueError("estimated workload cost must be positive")
        if not isinstance(self.criteria, tuple) or not self.criteria:
            raise ValueError("workload must declare graph-selection criteria")
        if not all(isinstance(item, GraphSelectionCriterion) for item in self.criteria):
            raise ValueError("workload criteria must be typed graph criteria")
        if not self.default_reason.strip() or any(
            ord(character) < 32 for character in self.default_reason
        ):
            raise ValueError("workload default reason must be non-empty")
        if self.workload_type == "k6":
            if self.trusted_command is not None or self.trusted_load is None:
                raise ValueError("k6 workloads require trusted load configuration only")
        elif self.trusted_load is not None or self.trusted_command is None:
            raise ValueError("non-load workloads require a trusted command array")
        if self.trusted_command is not None:
            _validate_command(self.trusted_command)


@dataclass(frozen=True)
class WorkloadRegistry:
    """Validated local definitions and commands under a single revision."""

    revision: str
    entries: tuple[WorkloadDefinition, ...]
    limits: RegistryLimits = RegistryLimits()

    def __post_init__(self) -> None:
        if not self.revision.strip():
            raise ValueError("registry revision must be non-empty")
        if not isinstance(self.entries, tuple) or not all(
            isinstance(entry, WorkloadDefinition) for entry in self.entries
        ):
            raise ValueError("registry entries must be immutable workload definitions")
        if len(self.entries) > self.limits.max_entries:
            raise ValueError("registry entry limit exceeded")
        identifiers = [entry.workload_id for entry in self.entries]
        duplicates = sorted(key for key, count in Counter(identifiers).items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate workload IDs: {', '.join(duplicates)}")
        for entry in self.entries:
            if len(entry.criteria) > self.limits.max_criteria_per_entry:
                raise ValueError(f"criterion limit exceeded for {entry.workload_id}")
            if len(entry.definition_path.encode("utf-8")) > self.limits.max_definition_path_bytes:
                raise ValueError(f"definition path limit exceeded for {entry.workload_id}")
            if entry.estimated_cost_seconds > self.limits.max_entry_cost_seconds:
                raise ValueError(f"estimated cost limit exceeded for {entry.workload_id}")
            if entry.trusted_command is not None:
                if len(entry.trusted_command) > self.limits.max_command_arguments:
                    raise ValueError(f"command argument limit exceeded for {entry.workload_id}")
                size = sum(len(argument.encode("utf-8")) for argument in entry.trusted_command)
                if size > self.limits.max_command_bytes:
                    raise ValueError(f"command byte limit exceeded for {entry.workload_id}")

    def command_map(self) -> dict[str, tuple[str, ...]]:
        """Return copies of trusted command arrays for non-load execution only."""

        return {
            entry.workload_id: entry.trusted_command
            for entry in self.entries
            if entry.trusted_command is not None
        }


@dataclass(frozen=True, order=True)
class OmittedWorkload:
    """One candidate or graph request not present in the finalized plan."""

    workload_id: str
    category: OmissionCategory
    reason: str


@dataclass(frozen=True)
class PlanningBudgetUsage:
    selected_workloads: int
    estimated_cost_seconds: float
    max_selected_workloads: int
    max_total_estimated_cost_seconds: float


@dataclass(frozen=True)
class ValidationPlan:
    """Deterministic, serializable adaptive-validation planning result."""

    registry_revision: str
    repository_id: str
    commit_sha: str
    selected: tuple[WorkloadSelection, ...]
    omitted: tuple[OmittedWorkload, ...]
    unresolved_graph_information: tuple[str, ...]
    completeness: float
    confidence: float
    state: PlanState
    budget: PlanningBudgetUsage
    metadata: tuple[tuple[str, str], ...]

    def payload(self) -> dict[str, object]:
        return {
            "registry_revision": self.registry_revision,
            "repository_id": self.repository_id,
            "commit_sha": self.commit_sha,
            "selected": [item.model_dump(mode="json") for item in self.selected],
            "omitted": [asdict(item) for item in self.omitted],
            "unresolved_graph_information": list(self.unresolved_graph_information),
            "completeness": self.completeness,
            "confidence": self.confidence,
            "state": self.state,
            "budget": asdict(self.budget),
            "metadata": dict(self.metadata),
        }


def _validate_definition_path(path: str) -> None:
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or path != pure.as_posix()
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or re.match(r"[A-Za-z]:", path)
        or any(ord(character) < 32 for character in path)
    ):
        raise ValueError("definition_path must be a safe normalized repository-relative path")


def _validate_command(command: object) -> None:
    if not isinstance(command, tuple) or not command:
        raise ValueError("trusted command must be a non-empty tuple")
    if any(
        not isinstance(argument, str)
        or not argument
        or any(ord(character) < 32 for character in argument)
        for argument in command
    ):
        raise ValueError("trusted command arguments must be non-empty printable strings")


def fixture_workload_registry() -> WorkloadRegistry:
    """Return the checked-in registry for the broken-store vertical slice."""

    return WorkloadRegistry(
        revision=REGISTRY_REVISION,
        entries=(
            WorkloadDefinition(
                workload_id=_PYTEST_ID,
                workload_type="pytest",
                definition_path=_PYTEST_PATH,
                priority=100,
                estimated_cost_seconds=30,
                criteria=(
                    GraphSelectionCriterion("affected_tests", f"{_PYTEST_PATH}::", "prefix"),
                ),
                fallback_eligible=True,
                default_reason="Fixture checkout correctness gate.",
                trusted_command=_PYTEST_COMMAND,
            ),
            WorkloadDefinition(
                workload_id=_K6_ID,
                workload_type="k6",
                definition_path=_K6_PATH,
                priority=100,
                estimated_cost_seconds=120,
                criteria=(GraphSelectionCriterion("affected_endpoints", "POST /checkout"),),
                fallback_eligible=True,
                default_reason="Fixture checkout query-count workload.",
                trusted_load=TrustedLoadConfiguration(),
            ),
        ),
    )


def fixture_workloads() -> tuple[WorkloadSelection, ...]:
    """Return compatibility records for the checked-in fixture registry."""

    registry = fixture_workload_registry()
    return tuple(
        _selection(entry, entry.default_reason, 1.0, registry.revision)
        for entry in registry.entries
    )


def fixture_commands() -> dict[str, tuple[str, ...]]:
    """Return trusted registry command arrays, never executable CLI input."""

    return fixture_workload_registry().command_map()


def plan_validation_workloads(
    context: RepositoryContext,
    registry: WorkloadRegistry,
    *,
    limits: PlanningLimits | None = None,
    fallback_workload_ids: tuple[str, ...] = (),
) -> ValidationPlan:
    """Finalize the safest relevant registry workload set from graph evidence."""

    active = limits or PlanningLimits()
    ordered_entries = sorted(
        registry.entries,
        key=lambda entry: (entry.priority, _TYPE_ORDER[entry.workload_type], entry.workload_id),
    )
    fallback = frozenset(fallback_workload_ids)
    known = {entry.workload_id for entry in registry.entries}
    omitted: list[OmittedWorkload] = []
    selected: list[WorkloadSelection] = []
    used_cost = 0.0

    counts = Counter(context.selected_workload_ids)
    for workload_id in sorted(key for key, count in counts.items() if count > 1):
        omitted.append(
            OmittedWorkload(
                workload_id,
                "duplicate",
                "Duplicate graph-selected workload ID was suppressed deterministically.",
            )
        )
    for workload_id in sorted((set(context.selected_workload_ids) | fallback) - known):
        omitted.append(
            OmittedWorkload(
                workload_id,
                "unregistered",
                "Graph or fallback requested an ID absent from the trusted registry.",
            )
        )

    incomplete = context.completeness < 1
    for entry in ordered_entries:
        fallback_selected = incomplete and entry.workload_id in fallback
        if incomplete:
            if not fallback_selected or not entry.fallback_eligible:
                omitted.append(
                    OmittedWorkload(
                        entry.workload_id,
                        "incomplete_graph",
                        (
                            "Graph context is incomplete and this workload was not an explicitly "
                            "configured eligible fallback."
                        ),
                    )
                )
                continue
            reason = (
                "Configured trusted-registry fallback because repository graph extraction is "
                f"incomplete ({context.completeness:.3f} complete)."
            )
            confidence = context.completeness * 0.5
            evidence_source = "configured_fallback"
            evidence_value = entry.workload_id
        else:
            match = _match_entry(entry, context)
            if match is None:
                omitted.append(
                    OmittedWorkload(
                        entry.workload_id,
                        "node_evidence",
                        "No declared graph-selection criterion matched complete graph evidence.",
                    )
                )
                continue
            evidence_source, evidence_value, reason = match
            confidence = context.completeness

        if len(selected) >= active.max_selected_workloads:
            omitted.append(
                OmittedWorkload(
                    entry.workload_id,
                    "budget",
                    f"Maximum selected-workload budget {active.max_selected_workloads} reached.",
                )
            )
            continue
        if used_cost + entry.estimated_cost_seconds > active.max_total_estimated_cost_seconds:
            omitted.append(
                OmittedWorkload(
                    entry.workload_id,
                    "budget",
                    (
                        f"Estimated cost {entry.estimated_cost_seconds:g}s would exceed total "
                        f"budget {active.max_total_estimated_cost_seconds:g}s."
                    ),
                )
            )
            continue
        selected.append(
            _selection(
                entry,
                reason,
                confidence,
                registry.revision,
                fallback=fallback_selected,
                evidence_source=evidence_source,
                evidence_value=evidence_value,
            )
        )
        used_cost += entry.estimated_cost_seconds

    budget = PlanningBudgetUsage(
        selected_workloads=len(selected),
        estimated_cost_seconds=used_cost,
        max_selected_workloads=active.max_selected_workloads,
        max_total_estimated_cost_seconds=active.max_total_estimated_cost_seconds,
    )
    state = _plan_state(context, selected, omitted)
    confidence = min((item.confidence for item in selected), default=context.completeness)
    if incomplete and not selected:
        confidence = 0.0
    return ValidationPlan(
        registry_revision=registry.revision,
        repository_id=context.repository_id,
        commit_sha=context.commit_sha,
        selected=tuple(selected),
        omitted=tuple(sorted(omitted)),
        unresolved_graph_information=tuple(sorted(set(context.unresolved_relationships))),
        completeness=context.completeness,
        confidence=confidence,
        state=state,
        budget=budget,
        metadata=(
            ("ordering_policy", "priority,type(pytest,semgrep,custom,k6),workload_id"),
            ("planner", "lou.repository.workloads.local-v1"),
        ),
    )


def select_fixture_workloads(
    context: RepositoryContext,
    *,
    fallback_workload_ids: tuple[str, ...] = (),
) -> tuple[WorkloadSelection, ...]:
    """Backward-compatible adapter over the adaptive fixture plan."""

    return plan_validation_workloads(
        context,
        fixture_workload_registry(),
        fallback_workload_ids=fallback_workload_ids,
    ).selected


def _selection(
    entry: WorkloadDefinition,
    reason: str,
    confidence: float,
    registry_revision: str,
    *,
    fallback: bool = False,
    evidence_source: str = "registry_default",
    evidence_value: str = "",
) -> WorkloadSelection:
    metadata: dict[str, object] = {
        "registry_revision": registry_revision,
        "priority": entry.priority,
        "estimated_cost_seconds": entry.estimated_cost_seconds,
        "fallback": fallback,
        "graph_evidence_source": evidence_source,
        "graph_evidence_value": evidence_value,
    }
    if entry.trusted_load is not None:
        metadata["load_configuration"] = asdict(entry.trusted_load)
    return WorkloadSelection(
        workload_id=entry.workload_id,
        workload_type=entry.workload_type,
        definition_path=entry.definition_path,
        phase="candidate",
        reason=reason,
        confidence=confidence,
        metadata=metadata,
    )


def _match_entry(
    entry: WorkloadDefinition, context: RepositoryContext
) -> tuple[str, str, str] | None:
    if entry.workload_id in context.selected_workload_ids:
        reason = context.selection_reasons.get(entry.workload_id) or (
            f"Graph explicitly selected registered workload ID {entry.workload_id}."
        )
        return "selected_workload_ids", entry.workload_id, reason
    for criterion in entry.criteria:
        for evidence_value, evidence_reason in _criterion_evidence(context, criterion.source):
            if not _matches(criterion, evidence_value):
                continue
            reason = evidence_reason or (
                f"Matched registered graph criterion {criterion.source}={evidence_value}."
            )
            return criterion.source, evidence_value, reason
    return None


def _criterion_evidence(
    context: RepositoryContext, source: CriterionSource
) -> tuple[tuple[str, str | None], ...]:
    if source == "selection_reasons":
        return tuple(
            (reason, reason)
            for _, reason in sorted(context.selection_reasons.items())
            if reason.strip()
        )
    values = getattr(context, source)
    return tuple(
        (value, context.selection_reasons.get(value))
        for value in sorted(set(values))
        if value.strip()
    )


def _matches(criterion: GraphSelectionCriterion, evidence: str) -> bool:
    if criterion.match == "exact":
        return evidence == criterion.value
    if criterion.match == "prefix":
        return evidence.startswith(criterion.value)
    return criterion.value.casefold() in evidence.casefold()


def _plan_state(
    context: RepositoryContext,
    selected: list[WorkloadSelection],
    omitted: list[OmittedWorkload],
) -> PlanState:
    if not selected and any(item.category == "budget" for item in omitted):
        return "budget_exhausted"
    if context.completeness < 1:
        return "incomplete_with_fallback" if selected else "incomplete_no_fallback"
    if selected:
        return "planned"
    return "complete_no_applicable"


__all__ = [
    "GraphSelectionCriterion",
    "OmittedWorkload",
    "PlanningBudgetUsage",
    "PlanningLimits",
    "REGISTRY_REVISION",
    "RegistryLimits",
    "TrustedLoadConfiguration",
    "ValidationPlan",
    "WorkloadDefinition",
    "WorkloadRegistry",
    "fixture_commands",
    "fixture_workload_registry",
    "fixture_workloads",
    "plan_validation_workloads",
    "select_fixture_workloads",
]
