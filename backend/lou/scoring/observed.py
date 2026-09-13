"""Derive scoring inputs from measurements, never from assumed constants.

Every function here returns only the features its argument actually supports. A
feature nothing measured is absent, so the scorer records it as unknown and lowers
confidence. That is deliberate: asserting a plausible number instead would let a
score clear an autonomy gate on evidence that was never collected.
"""

from __future__ import annotations

from contracts import Finding, PatchArtifact, RepositoryContext, VerificationResult

RUNTIME_REGRESSION = "runtime-regression"

# Saturation points for counts that have no natural upper bound. They set the scale
# of a measurement; they are not stand-ins for one.
_CENTRALITY_REACH = 10.0
_PATCH_BUDGET_SYMBOLS = 5.0
_PATCH_BUDGET_LINES = 200.0
_UNSERVED_PATH_CRITICALITY = 0.4


def graph_debt_features(context: RepositoryContext) -> dict[str, float]:
    """Return the debt features the repository graph measures.

    complexity, coverage_deficit, and churn are absent because nothing in this
    system measures them: no complexity metric, no coverage instrumentation, and no
    history analysis feeds this path.
    """

    reached = len(context.affected_symbols)
    changed = max(len(context.changed_symbols), 1)
    return {
        "graph_centrality": min(reached / _CENTRALITY_REACH, 1.0),
        "path_criticality": 1.0 if context.affected_endpoints else _UNSERVED_PATH_CRITICALITY,
        "estimated_patch_size": min(changed / _PATCH_BUDGET_SYMBOLS, 1.0),
    }


def graph_remediation_features(context: RepositoryContext) -> dict[str, float]:
    """Return the remediation features the repository graph measures.

    coverage is absent because affected tests are not attributed to the symbols they
    exercise, so the graph cannot say how much of the change they cover.
    """

    reached = len(context.affected_symbols)
    return {
        "blast_radius": min(reached / _CENTRALITY_REACH, 1.0),
        "criticality": 1.0 if context.affected_endpoints else _UNSERVED_PATH_CRITICALITY,
        "context_completeness": context.completeness,
    }


def measured_runtime_impact(finding: Finding) -> dict[str, float]:
    """Return runtime impact from the classification the comparison actually recorded.

    A regression finding is emitted only after baseline and candidate were measured
    and compared, so its category is an observation rather than an assumption.
    """

    return {"runtime_impact": 1.0 if finding.category == RUNTIME_REGRESSION else 0.0}


def patch_remediation_features(patch: PatchArtifact) -> dict[str, float]:
    """Return the remediation features a real proposed patch supports.

    Only size is here. Reversibility and migration risk depend on which files the diff
    touches, and a PatchArtifact records only counts, so this path cannot observe them.
    """

    touched = patch.lines_added + patch.lines_deleted
    return {"patch_size": min(touched / _PATCH_BUDGET_LINES, 1.0)}


def verification_strength(
    results: tuple[VerificationResult, ...], required_workload_ids: tuple[str, ...]
) -> dict[str, float]:
    """Return the share of the trusted plan that produced a passing fix result."""

    if not required_workload_ids:
        return {}
    passed = {
        result.workload_id
        for result in results
        if result.status == "passed" and result.workload_id is not None
    }
    required = set(required_workload_ids)
    return {"verification_strength": len(passed & required) / len(required)}
