"""Select an allowed action from observations and externally supplied verdicts."""

import subprocess
from collections.abc import Sequence
from hashlib import sha256
from typing import Any, Literal

from contracts import AnalysisJob, LouDecision, PatchArtifact, VerificationResult
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs, score_debt, score_remediation

RULE_REVISION = "autonomy-v1"
ACTIONS: tuple[Literal["report", "recommend", "generate_patch", "open_pr"], ...] = (
    "report",
    "recommend",
    "generate_patch",
    "open_pr",
)


def decide_autonomy(
    *,
    decision_id: str,
    analysis_run_id: str,
    debt_inputs: DebtInputs,
    remediation_inputs: RemediationInputs | None = None,
    policy: AutonomyPolicy | None = None,
    patch: PatchArtifact | None = None,
    patch_content: bytes | None = None,
    analysis_job: AnalysisJob | None = None,
    expected_fix_commit_sha: str | None = None,
    expected_verification_attempt_id: str | None = None,
    required_workload_ids: Sequence[str] = (),
    verification_results: Sequence[VerificationResult] = (),
    candidate_regression: VerificationResult | None = None,
) -> LouDecision:
    """Return the shared LouDecision, never run tests, create a patch, or publish.

    Supply final fix-attempt results from the independent verifier and the trusted
    workload plan. The caller must pass the exact patch bytes it will publish.
    V1 binds each result to those bytes through metadata.patch_sha256 and
    metadata.verification_attempt_id.
    A candidate-phase failed result can establish observed regression priority.
    Inputs are rescored here so callers cannot accidentally reuse stale scores.
    """
    debt = score_debt(debt_inputs)
    remediation = score_remediation(remediation_inputs) if remediation_inputs is not None else None
    policy = (
        policy if policy is not None else AutonomyPolicy(revision="missing-policy", max_autonomy=0)
    )
    confidence = min(debt.confidence, remediation.confidence) if remediation else 0.0
    confirmed_regression = (
        candidate_regression is not None
        and candidate_regression.phase == "candidate"
        and candidate_regression.status == "failed"
        and candidate_regression.analysis_run_id == analysis_run_id
        and (
            analysis_job is None
            or candidate_regression.commit_sha == analysis_job.candidate_commit_sha
        )
    )
    gates: list[dict[str, Any]] = []
    level = 3

    def gate(
        code: str, passed: bool, ceiling: int, reason: str, observed: Any, required: Any
    ) -> None:
        nonlocal level
        gates.append(
            {
                "code": code,
                "passed": passed,
                "ceiling_on_failure": ceiling,
                "reason": reason,
                "observed": observed,
                "required": required,
            }
        )
        if not passed:
            level = min(level, ceiling)

    gate(
        "debt_priority",
        debt.debt_risk >= 0.20 or confirmed_regression,
        0,
        "Debt risk must reach 0.20 or a candidate regression must be confirmed.",
        {"debt_risk": debt.debt_risk, "confirmed_candidate_regression": confirmed_regression},
        {"minimum_debt_risk": 0.20, "or_confirmed_candidate_regression": True},
    )
    gate(
        "recommend_confidence",
        confidence >= 0.50,
        0,
        "Confidence must reach 0.50 to recommend action.",
        confidence,
        {"minimum": 0.50},
    )
    gate(
        "remediation_assessed",
        remediation is not None,
        1,
        "Local patch generation needs a remediation assessment.",
        remediation is not None,
        True,
    )

    missing = [f"debt.{name}" for name in debt.missing_inputs]
    if remediation is not None:
        missing.extend(f"remediation.{name}" for name in remediation.missing_inputs)
    else:
        missing.append("remediation_inputs")
    context = remediation_inputs.context_completeness if remediation_inputs else None
    gate(
        "complete_context",
        not missing and context == 1.0,
        1,
        "Missing observations or incomplete context limit autonomy to recommendations.",
        {"missing_inputs": missing, "context_completeness": context},
        {"missing_inputs": [], "context_completeness": 1.0},
    )
    gate(
        "patch_confidence",
        confidence >= 0.75,
        1,
        "Local patch generation requires confidence of at least 0.75.",
        confidence,
        {"minimum": 0.75},
    )
    risk = remediation.remediation_risk if remediation else None
    gate(
        "patch_risk",
        risk is not None and risk <= 0.40,
        1,
        "Local patch generation requires remediation risk at most 0.40.",
        risk,
        {"maximum": 0.40},
    )

    if remediation_inputs is not None:
        for name in ("blast_radius", "criticality", "patch_size"):
            value = getattr(remediation_inputs, name)
            gate(
                f"bounded_{name}",
                value is not None and value < 0.80,
                1,
                f"{name.replace('_', ' ').capitalize()} of 0.80 or more needs human planning.",
                value,
                {"exclusive_maximum": 0.80},
            )
        for name in ("schema_migration_risk", "data_migration_risk"):
            value = getattr(remediation_inputs, name)
            gate(
                f"no_{name}",
                value == 0,
                1,
                "Schema and data migrations require human planning in this rule revision.",
                value,
                0,
            )
        value = remediation_inputs.reversibility
        gate(
            "patch_reversible",
            value is not None and value >= 0.50,
            1,
            "Local patch generation requires reversibility of at least 0.50.",
            value,
            {"minimum": 0.50},
        )
        for name in ("coverage", "reversibility", "verification_strength"):
            value = getattr(remediation_inputs, name)
            gate(
                f"pr_{name}",
                value is not None and value >= 0.80,
                2,
                f"Opening a PR requires {name.replace('_', ' ')} of at least 0.80.",
                value,
                {"minimum": 0.80},
            )

    gate(
        "pr_confidence",
        confidence >= 0.90,
        2,
        "Opening a PR requires confidence of at least 0.90.",
        confidence,
        {"minimum": 0.90},
    )
    gate(
        "pr_risk",
        risk is not None and risk <= 0.20,
        2,
        "Opening a PR requires remediation risk at most 0.20.",
        risk,
        {"maximum": 0.20},
    )
    patch_matches = (
        patch is not None
        and patch.analysis_run_id == analysis_run_id
        and bool(patch.patch_sha256)
        and patch.files_changed > 0
        and patch.lines_added + patch.lines_deleted > 0
    )
    gate(
        "patch_identity",
        patch_matches,
        2,
        "Opening a PR requires a nonempty patch belonging to this analysis run.",
        patch.model_dump(mode="json") if patch else None,
        {"analysis_run_id": analysis_run_id},
    )
    gate(
        "verification_present",
        bool(verification_results),
        2,
        "Opening a PR requires independent fix verification results.",
        len(verification_results),
        {"minimum": 1},
    )
    gate(
        "verification_passed",
        bool(verification_results) and all(r.status == "passed" for r in verification_results),
        2,
        "Every supplied verification verdict must be passed; failed or inconclusive forbids A3.",
        [r.status for r in verification_results],
        "all passed",
    )
    gate(
        "fix_identity",
        bool(expected_fix_commit_sha)
        and bool(verification_results)
        and all(
            r.phase == "fix"
            and r.analysis_run_id == analysis_run_id
            and r.commit_sha == expected_fix_commit_sha
            for r in verification_results
        ),
        2,
        "Verification must belong to this run and the exact expected fix commit.",
        [
            {"phase": r.phase, "analysis_run_id": r.analysis_run_id, "commit_sha": r.commit_sha}
            for r in verification_results
        ],
        {"phase": "fix", "analysis_run_id": analysis_run_id, "commit_sha": expected_fix_commit_sha},
    )
    gate(
        "verified_patch_binding",
        patch_matches
        and bool(verification_results)
        and all(
            r.metadata.get("patch_sha256") == patch.patch_sha256
            for r in verification_results
            if patch is not None
        ),
        2,
        "The independent verifier must identify the exact patch hash in every result.",
        [r.metadata.get("patch_sha256") for r in verification_results],
        patch.patch_sha256 if patch else None,
    )
    required = set(required_workload_ids)
    observed = {r.workload_id for r in verification_results if r.workload_id is not None}
    gate(
        "required_workloads",
        bool(required) and all(required) and required <= observed,
        2,
        "Every workload in the trusted verification plan must have a result.",
        sorted(observed),
        sorted(required),
    )
    gate(
        "candidate_regression_evidence",
        confirmed_regression,
        2,
        "Opening a PR requires a failed candidate-phase result from this analysis run.",
        candidate_regression.model_dump(mode="json") if candidate_regression else None,
        {
            "phase": "candidate",
            "status": "failed",
            "analysis_run_id": analysis_run_id,
            "commit_sha": analysis_job.candidate_commit_sha if analysis_job else None,
        },
    )
    identity_failures: list[dict[str, Any]] = []

    def compare_identity(code: str, expected: Any, actual: Any) -> None:
        if expected is None or actual is None or expected != actual:
            identity_failures.append({"code": code, "expected": expected, "actual": actual})

    computed_hash = sha256(patch_content).hexdigest() if patch_content else None
    if candidate_regression is not None:
        compare_identity(
            "candidate_regression_run", analysis_run_id, candidate_regression.analysis_run_id
        )
        if analysis_job is not None:
            compare_identity(
                "candidate_regression_commit",
                analysis_job.candidate_commit_sha,
                candidate_regression.commit_sha,
            )
    if patch is not None:
        compare_identity("patch_content_hash", patch.patch_sha256, computed_hash)
        if analysis_job is None:
            compare_identity("analysis_job", analysis_run_id, None)
        else:
            compare_identity("analysis_run", analysis_run_id, analysis_job.analysis_run_id)
            compare_identity("patch_run", analysis_job.analysis_run_id, patch.analysis_run_id)
            compare_identity(
                "patch_base_commit", analysis_job.candidate_commit_sha, patch.base_commit_sha
            )
            if expected_fix_commit_sha is not None:
                ancestry_code: str | None
                try:
                    ancestry_result = subprocess.run(
                        [
                            "git",
                            "-C",
                            analysis_job.repository_path,
                            "merge-base",
                            "--is-ancestor",
                            analysis_job.candidate_commit_sha,
                            expected_fix_commit_sha,
                        ],
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    ancestry_code = "fix_commit_ancestry_unavailable"
                else:
                    if ancestry_result.returncode == 0:
                        ancestry_code = None
                    elif ancestry_result.returncode == 1:
                        ancestry_code = "fix_commit_ancestry_failed"
                    else:
                        ancestry_code = "fix_commit_ancestry_unavailable"
                if ancestry_code is not None:
                    identity_failures.append(
                        {
                            "code": ancestry_code,
                            "reason": (
                                "Ancestry could not be determined."
                                if ancestry_code == "fix_commit_ancestry_unavailable"
                                else "The fix commit does not descend from the analysis candidate."
                            ),
                            "expected": analysis_job.candidate_commit_sha,
                            "actual": expected_fix_commit_sha,
                        }
                    )
        for result in verification_results:
            compare_identity("workload_phase", "fix", result.phase)
            compare_identity("workload_analysis_run", patch.analysis_run_id, result.analysis_run_id)
            compare_identity(
                "workload_patch_hash", computed_hash, result.metadata.get("patch_sha256")
            )
            compare_identity(
                "workload_verification_attempt",
                expected_verification_attempt_id,
                result.metadata.get("verification_attempt_id"),
            )
            compare_identity("workload_fix_commit", expected_fix_commit_sha, result.commit_sha)
        if verification_results and analysis_job is not None:
            planned = analysis_job.verification_plan.get("workloads")
            planned_ids = (
                sorted(planned)
                if isinstance(planned, list) and all(isinstance(item, str) for item in planned)
                else None
            )
            compare_identity(
                "verification_plan_workloads",
                planned_ids,
                sorted(required),
            )
            if required and not required <= observed:
                identity_failures.append(
                    {
                        "code": "workload_results",
                        "expected": sorted(required),
                        "actual": sorted(observed),
                    }
                )
    gate(
        "evidence_identity",
        not identity_failures,
        0,
        "Evidence identity mismatch declines this action.",
        identity_failures,
        [],
    )
    before_policy = level
    gate(
        "policy_ceiling",
        level <= policy.max_autonomy,
        policy.max_autonomy,
        "The policy ceiling can only lower the evidence-selected autonomy.",
        level,
        {"maximum": policy.max_autonomy},
    )

    return LouDecision(
        decision_id=decision_id,
        analysis_run_id=analysis_run_id,
        debt_risk=debt.debt_risk,
        remediation_risk=risk,
        confidence=confidence,
        autonomy_level=level,
        action=ACTIONS[level],
        rationale={
            "summary": (
                "A0: declined because evidence identities disagree."
                if identity_failures
                else f"A{level}: {ACTIONS[level].replace('_', ' ')}."
            ),
            "reasons": [item["reason"] for item in gates if not item["passed"]]
            or ["Complete evidence, low remediation risk, and passed fix verification permit A3."],
            "gates": gates,
            "missing_inputs": missing,
            "declined": bool(identity_failures),
            "decline_reasons": identity_failures,
        },
        metadata={
            "rule_revision": RULE_REVISION,
            "policy_revision": policy.revision,
            "policy": policy.model_dump(mode="json"),
            "autonomy_before_policy": before_policy,
            "scores": {
                "debt": debt.model_dump(mode="json"),
                "remediation": remediation.model_dump(mode="json") if remediation else None,
            },
            "patch_id": patch.patch_id if patch else None,
            "computed_patch_sha256": computed_hash,
            "analysis_candidate_commit_sha": (
                analysis_job.candidate_commit_sha if analysis_job else None
            ),
            "expected_fix_commit_sha": expected_fix_commit_sha,
            "expected_verification_attempt_id": expected_verification_attempt_id,
            "required_workload_ids": sorted(required),
            "verification_results": [r.model_dump(mode="json") for r in verification_results],
            "candidate_regression": (
                candidate_regression.model_dump(mode="json") if candidate_regression else None
            ),
            "signals": {
                "debt": {
                    term.feature: {
                        "raw_value": term.observed_value,
                        "normalized_value": term.normalized_value,
                        "missing": term.missing,
                    }
                    for component in (
                        debt.components["principal"],
                        debt.components["interest_rate"],
                    )
                    for term in component.contributions
                },
                "remediation": {
                    term.feature: {
                        "raw_value": term.observed_value,
                        "normalized_value": term.normalized_value,
                        "missing": term.missing,
                    }
                    for term in remediation.components["remediation_risk"].contributions
                }
                if remediation
                else None,
                "missing_inputs": missing,
            },
        },
    )
