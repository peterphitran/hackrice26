"""M5 assessment and policy integration around the existing verified decision."""

from collections.abc import Sequence

from contracts import (
    AnalysisJob,
    AutonomyDecision,
    CostEstimates,
    LouDecision,
    PatchArtifact,
    RiskSignals,
    VerificationResult,
)
from lou.decision.autonomy import decide_autonomy
from lou.decision.patch_risk import patch_risk_flags
from lou.policies import AutonomyPolicy
from lou.policies.engine import LocalPolicy, PolicyEvaluator, PolicyInput
from lou.scoring import DebtInputs, RemediationInputs
from lou.scoring.assessment import (
    apply_hard_risk_flags,
    assess_debt,
    assess_remediation,
    estimate_value,
)

ACTIONS = ("report", "recommend", "generate_patch", "open_pr", "auto_merge", "auto_deploy")


def decide_m5(
    *,
    decision_id: str,
    analysis_run_id: str,
    debt_inputs: DebtInputs,
    remediation_inputs: RemediationInputs | None = None,
    risk_signals: RiskSignals | None = None,
    cost_estimates: CostEstimates | None = None,
    policy_evaluator: PolicyEvaluator | None = None,
    policy_revision: str = "local-v1",
    patch: PatchArtifact | None = None,
    patch_content: bytes | None = None,
    analysis_job: AnalysisJob | None = None,
    expected_fix_commit_sha: str | None = None,
    expected_verification_attempt_id: str | None = None,
    required_workload_ids: Sequence[str] = (),
    verification_results: Sequence[VerificationResult] = (),
    candidate_regression: VerificationResult | None = None,
) -> LouDecision:
    """Return a v1-compatible decision containing the full version-two M5 record.

    The v1 decision supplies the independent verification and exact-patch identity
    gates. M5 can only reduce its level. Local safety rules run even when OPA is
    selected, so an external policy response cannot lift a hard safety cap.
    """
    base = decide_autonomy(
        decision_id=decision_id,
        analysis_run_id=analysis_run_id,
        debt_inputs=debt_inputs,
        remediation_inputs=remediation_inputs,
        policy=AutonomyPolicy(revision=policy_revision, max_autonomy=3),
        patch=patch,
        patch_content=patch_content,
        analysis_job=analysis_job,
        expected_fix_commit_sha=expected_fix_commit_sha,
        expected_verification_attempt_id=expected_verification_attempt_id,
        required_workload_ids=required_workload_ids,
        verification_results=verification_results,
        candidate_regression=candidate_regression,
    )
    patch_flags = patch_risk_flags(patch_content)
    signals = risk_signals or RiskSignals()
    if signals.security_sensitive is None and "patch_paths_unknown" not in patch_flags:
        signals = signals.model_copy(
            update={
                "security_sensitive": "security_sensitive_file" in patch_flags,
                "sources": {**signals.sources, "security_sensitive": "verified_patch_paths"},
            }
        )
    debt = assess_debt(analysis_run_id, debt_inputs, signals)
    remediation = (
        assess_remediation(analysis_run_id, remediation_inputs, signals)
        if remediation_inputs is not None
        else None
    )
    if remediation is not None and patch_flags:
        remediation = apply_hard_risk_flags(remediation, patch_flags)
    value = estimate_value(cost_estimates)
    request = PolicyInput(
        analysis_run_id=analysis_run_id,
        requested_revision=policy_revision,
        evidence_level=base.autonomy_level,
        remediation=remediation,
        expected_value=value,
        verification_statuses=tuple(result.status for result in verification_results),
    )
    safety = LocalPolicy(revision=policy_revision, max_autonomy=5).evaluate(request)
    organization = (policy_evaluator or LocalPolicy(revision=policy_revision)).evaluate(request)
    reasons = [*safety.deny_reasons, *organization.deny_reasons]
    if organization.denied and not organization.deny_reasons:
        reasons.append("organizational_policy_denied")
    revision_mismatch = organization.revision != policy_revision or (
        analysis_job is not None and analysis_job.policy_revision != policy_revision
    )
    if revision_mismatch:
        reasons.append("policy_revision_mismatch")
    if organization.denied or revision_mismatch:
        organization_ceiling = 0
    else:
        organization_ceiling = organization.max_autonomy
    confidence = min(debt.confidence, remediation.confidence) if remediation else 0.0
    evidence_ceiling = 0 if confidence < 0.5 else 1 if confidence < 0.75 else 3
    if evidence_ceiling < base.autonomy_level:
        reasons.append("m5_confidence_below_autonomy_threshold")
    level = min(base.autonomy_level, safety.max_autonomy, organization_ceiling, evidence_ceiling, 3)
    reasons = list(dict.fromkeys(reasons))
    record = AutonomyDecision(
        decision_id=decision_id,
        analysis_run_id=analysis_run_id,
        rule_revision="autonomy-v2",
        requested_policy_revision=policy_revision,
        evaluated_policy_revision=organization.revision,
        evidence_level=base.autonomy_level,
        organization_ceiling=organization_ceiling,
        permitted_level=level,
        action=ACTIONS[level],  # type: ignore[arg-type]
        denied_reasons=tuple(reasons),
        debt=debt,
        remediation=remediation,
        expected_value=value,
    )
    rationale = {
        **base.rationale,
        "summary": f"A{level}: {ACTIONS[level].replace('_', ' ')}.",
        "reasons": reasons or base.rationale.get("reasons", []),
        "m5_reasons": reasons,
        "declined": bool(base.rationale.get("declined")) or (level == 0 and bool(reasons)),
    }
    metadata = {**base.metadata, "m5": record.model_dump(mode="json")}
    return LouDecision.model_validate(
        {
            **base.model_dump(mode="json"),
            "confidence": confidence,
            "autonomy_level": level,
            "action": ACTIONS[level],
            "rationale": rationale,
            "metadata": metadata,
        }
    )
