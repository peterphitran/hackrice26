import pytest

from contracts import Finding, PatchArtifact, RepositoryContext, VerificationResult
from lou.decision import decide_autonomy
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs, score_remediation
from lou.scoring.observed import (
    graph_debt_features,
    graph_remediation_features,
    measured_runtime_impact,
    patch_remediation_features,
    verification_strength,
)

UNMEASURED_DEBT = {"complexity", "coverage_deficit", "churn"}
UNMEASURED_REMEDIATION = {
    "coverage",
    "reversibility",
    "verification_strength",
    "patch_size",
    "schema_migration_risk",
    "data_migration_risk",
}


def _context(**updates: object) -> RepositoryContext:
    values: dict[str, object] = {
        "repository_id": "demo",
        "commit_sha": "a" * 40,
        "changed_symbols": ["checkout"],
        "affected_symbols": ["checkout", "cart"],
        "affected_endpoints": ["POST /checkout"],
        "completeness": 1.0,
    }
    values.update(updates)
    return RepositoryContext.model_validate(values)


def _finding(category: str = "runtime-regression") -> Finding:
    return Finding(
        finding_id="finding-1",
        analysis_run_id="run-1",
        fingerprint="sha256:demo",
        source="lou.verification.compare",
        category=category,
        severity="high",
        confidence=0.9,
        phase="candidate",
        title="N+1 query",
        message="Query count regressed.",
    )


def _patch(lines_added: int = 10, lines_deleted: int = 10) -> PatchArtifact:
    return PatchArtifact(
        patch_id="patch-1",
        analysis_run_id="run-1",
        base_commit_sha="a" * 40,
        patch_sha256="b" * 64,
        artifact_uri="file:///tmp/patch.diff",
        files_changed=1,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
    )


def _result(workload_id: str, status: str = "passed") -> VerificationResult:
    return VerificationResult(
        verification_run_id=f"verify-{workload_id}",
        analysis_run_id="run-1",
        phase="fix",
        commit_sha="c" * 40,
        status=status,  # type: ignore[arg-type]
        workload_id=workload_id,
    )


def test_debt_features_omit_everything_the_graph_does_not_measure() -> None:
    features = graph_debt_features(_context())

    assert UNMEASURED_DEBT.isdisjoint(features)
    assert set(features) == {"graph_centrality", "path_criticality", "estimated_patch_size"}


def test_omitted_debt_features_stay_unknown_rather_than_becoming_zero() -> None:
    inputs = DebtInputs.model_validate(graph_debt_features(_context()))

    assert inputs.complexity is None
    assert inputs.coverage_deficit is None
    assert inputs.churn is None


def test_graph_centrality_scales_with_how_far_the_change_reaches() -> None:
    narrow = graph_debt_features(_context(affected_symbols=["checkout"]))
    wide = graph_debt_features(_context(affected_symbols=[f"symbol-{n}" for n in range(20)]))

    assert narrow["graph_centrality"] < wide["graph_centrality"]
    assert wide["graph_centrality"] == 1.0


def test_a_change_off_any_served_endpoint_is_less_path_critical() -> None:
    served = graph_debt_features(_context())
    unserved = graph_debt_features(_context(affected_endpoints=[]))

    assert served["path_criticality"] == 1.0
    assert unserved["path_criticality"] < served["path_criticality"]


def test_remediation_features_omit_everything_the_graph_does_not_measure() -> None:
    features = graph_remediation_features(_context())

    assert UNMEASURED_REMEDIATION.isdisjoint(features)
    assert set(features) == {"blast_radius", "criticality", "context_completeness"}


def test_remediation_features_carry_the_measured_context_completeness() -> None:
    features = graph_remediation_features(_context(completeness=0.5))

    assert features["context_completeness"] == 0.5


def test_runtime_impact_follows_the_recorded_regression_classification() -> None:
    assert measured_runtime_impact(_finding())["runtime_impact"] == 1.0
    assert measured_runtime_impact(_finding("correctness"))["runtime_impact"] == 0.0


def test_patch_size_is_measured_from_the_real_diff() -> None:
    assert (
        patch_remediation_features(_patch(1, 1))["patch_size"]
        < (patch_remediation_features(_patch(50, 50))["patch_size"])
    )
    assert patch_remediation_features(_patch(500, 500))["patch_size"] == 1.0


def test_a_patch_supplies_only_the_feature_its_record_measures() -> None:
    assert set(patch_remediation_features(_patch())) == {"patch_size"}


def test_verification_strength_is_the_share_of_the_plan_that_passed() -> None:
    required = ("checkout-pytest", "checkout-k6")

    assert verification_strength((_result("checkout-pytest"),), required) == {
        "verification_strength": 0.5
    }
    assert verification_strength(
        (_result("checkout-pytest"), _result("checkout-k6")), required
    ) == {"verification_strength": 1.0}


def test_a_failed_workload_does_not_count_toward_verification_strength() -> None:
    strength = verification_strength(
        (_result("checkout-pytest"), _result("checkout-k6", "failed")),
        ("checkout-pytest", "checkout-k6"),
    )

    assert strength == {"verification_strength": 0.5}


def test_verification_strength_is_unknown_without_a_trusted_plan() -> None:
    assert verification_strength((_result("checkout-pytest"),), ()) == {}


@pytest.mark.parametrize("feature", sorted(UNMEASURED_REMEDIATION))
def test_an_unmeasured_remediation_feature_raises_risk_instead_of_lowering_it(
    feature: str,
) -> None:
    """Absence must be conservative: the constants this replaced all did the opposite."""

    known = {
        "blast_radius": 0.1,
        "criticality": 0.1,
        "coverage": 0.9,
        "reversibility": 1.0,
        "verification_strength": 1.0,
        "patch_size": 0.1,
        "schema_migration_risk": 0.0,
        "data_migration_risk": 0.0,
        "context_completeness": 1.0,
        "evidence_confidence": 1.0,
    }
    complete = score_remediation(RemediationInputs.model_validate(known))
    withheld = score_remediation(RemediationInputs.model_validate({**known, feature: None}))

    assert withheld.remediation_risk > complete.remediation_risk
    assert withheld.confidence < complete.confidence


def test_measured_inputs_do_not_reach_the_autonomy_level_the_old_constants_did() -> None:
    """The fixture change is small and well contained, so Lou may only report it.

    The constants this replaced asserted 0.8 complexity, 0.6 churn and 0.9 evidence on a
    one-symbol change, which cleared A2 and authorized patch generation. Measuring the
    same change instead yields a low debt risk, and low confidence because nothing here
    observes complexity, coverage or churn. Raising this again should require new
    measurement, not new constants.
    """

    debt = DebtInputs.model_validate(
        {
            **graph_debt_features(_context()),
            **measured_runtime_impact(_finding()),
            "evidence_confidence": 0.9,
        }
    )
    remediation = RemediationInputs.model_validate(
        {**graph_remediation_features(_context()), "evidence_confidence": 0.9}
    )

    decision = decide_autonomy(
        decision_id="decision-1",
        analysis_run_id="run-1",
        debt_inputs=debt,
        remediation_inputs=remediation,
        policy=AutonomyPolicy(revision="test-v1", max_autonomy=2),
    )

    assert decision.action == "report"
    assert decision.autonomy_level == 0
    assert decision.debt_risk < 0.20
    assert decision.confidence < 0.50
