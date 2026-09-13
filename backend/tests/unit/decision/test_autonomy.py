import subprocess
from hashlib import sha256
from itertools import product
from pathlib import Path
from random import Random
from unittest.mock import patch as mock_patch

import pytest
from pydantic import ValidationError

from contracts import (
    AnalysisJob,
    Finding,
    LouDecision,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
)
from lou.decision import decide_autonomy
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs, score_remediation

DEBT = {
    "complexity": 0.8,
    "coverage_deficit": 0.4,
    "estimated_patch_size": 0.2,
    "churn": 0.6,
    "graph_centrality": 0.7,
    "runtime_impact": 0.9,
    "path_criticality": 1.0,
    "evidence_confidence": 1.0,
}
REMEDIATION = {
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
PATCH: PatchArtifact
PATCH_CONTENT: bytes
ANALYSIS_JOB: AnalysisJob
FIX_SHA: str
FIX_UNRELATED_SHA: str


@pytest.fixture(scope="module", autouse=True)
def committed_patch(tmp_path_factory):
    global PATCH, PATCH_CONTENT, ANALYSIS_JOB, FIX_SHA, FIX_UNRELATED_SHA
    repo = tmp_path_factory.mktemp("autonomy-repo")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "example.py"
    source.write_text("VALUE = 0\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.py"], check=True)
    commit = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
    ]
    subprocess.run([*commit, "baseline"], check=True)
    baseline = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    source.write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.py"], check=True)
    subprocess.run([*commit, "candidate"], check=True)
    candidate = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    source.write_text("VALUE = 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "example.py"], check=True)
    subprocess.run([*commit, "fix"], check=True)
    FIX_SHA = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    PATCH_CONTENT = subprocess.check_output(
        ["git", "-C", str(repo), "diff", "--binary", candidate, FIX_SHA]
    )
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{FIX_SHA}^{{tree}}"], text=True
    ).strip()
    FIX_UNRELATED_SHA = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit-tree",
            tree,
            "-m",
            "unrelated root",
        ],
        text=True,
    ).strip()
    PATCH = PatchArtifact(
        patch_id="patch-1",
        analysis_run_id="run-1",
        base_commit_sha=candidate,
        patch_sha256=sha256(PATCH_CONTENT).hexdigest(),
        artifact_uri="patches/fix.diff",
        files_changed=1,
        lines_added=1,
        lines_deleted=1,
    )
    ANALYSIS_JOB = AnalysisJob(
        analysis_run_id="run-1",
        repository_id="repo-1",
        repository_path=str(repo),
        base_commit_sha=baseline,
        candidate_commit_sha=candidate,
        verification_plan={"workloads": ["checkout"]},
    )


def result(**changes):
    return VerificationResult.model_validate(
        {
            "verification_run_id": "verify-1",
            "analysis_run_id": "run-1",
            "phase": "fix",
            "commit_sha": FIX_SHA,
            "status": "passed",
            "workload_id": "checkout",
            "metadata": {
                "patch_sha256": PATCH.patch_sha256,
                "verification_attempt_id": "attempt-1",
            },
        }
        | changes
    )


def candidate_result(**changes):
    return VerificationResult.model_validate(
        {
            "verification_run_id": "verify-candidate-1",
            "analysis_run_id": "run-1",
            "phase": "candidate",
            "commit_sha": ANALYSIS_JOB.candidate_commit_sha,
            "status": "failed",
            "workload_id": "checkout",
        }
        | changes
    )


def decide(**changes):
    return decide_autonomy(
        **(
            {
                "decision_id": "decision-1",
                "analysis_run_id": "run-1",
                "debt_inputs": DebtInputs(**DEBT),
                "remediation_inputs": RemediationInputs(**REMEDIATION),
                "policy": AutonomyPolicy(max_autonomy=3),
                "patch": PATCH,
                "patch_content": PATCH_CONTENT,
                "analysis_job": ANALYSIS_JOB,
                "expected_fix_commit_sha": FIX_SHA,
                "expected_verification_attempt_id": "attempt-1",
                "required_workload_ids": ["checkout"],
                "verification_results": [result()],
                "candidate_regression": candidate_result(),
            }
            | changes
        )
    )


@pytest.mark.parametrize(
    "changes,level,action",
    [
        ({"debt_inputs": DebtInputs()}, 0, "report"),
        ({"remediation_inputs": None}, 0, "report"),
        ({"debt_inputs": DebtInputs(**(DEBT | {"evidence_confidence": 0.6}))}, 1, "recommend"),
        ({"verification_results": []}, 2, "generate_patch"),
        ({}, 3, "open_pr"),
    ],
)
def test_four_actions(changes, level, action):
    decision = decide(**changes)
    assert decision.autonomy_level == level
    assert decision.action == action
    assert LouDecision.model_validate_json(decision.model_dump_json()) == decision


@pytest.mark.parametrize(
    "confidence,expected",
    [
        (0.0, 0),
        (0.499999, 0),
        (0.5, 1),
        (0.749999, 1),
        (0.75, 2),
        (0.899999, 2),
        (0.9, 3),
        (1.0, 3),
    ],
)
def test_confidence_boundaries(confidence, expected):
    decision = decide(debt_inputs=DebtInputs(**(DEBT | {"evidence_confidence": confidence})))
    assert decision.autonomy_level == expected
    assert decision.confidence == confidence


@pytest.mark.parametrize("principal,level", [(0.399999, 0), (0.4, 3), (1.0, 3)])
def test_debt_priority_boundary(principal, level):
    debt = DebtInputs(
        complexity=principal,
        coverage_deficit=principal,
        estimated_patch_size=principal,
        churn=0.0,
        graph_centrality=0.0,
        runtime_impact=0.0,
        path_criticality=0.0,
        evidence_confidence=1.0,
    )
    decision = decide(
        debt_inputs=debt,
        candidate_regression=None if principal < 0.4 else candidate_result(),
    )
    assert decision.autonomy_level == level


def test_measured_n_plus_one_regression_overrides_low_debt_priority():
    debt = DebtInputs(
        complexity=0.2,
        coverage_deficit=0.2,
        estimated_patch_size=0.1,
        churn=1.0,
        graph_centrality=1.0,
        runtime_impact=1.0,
        path_criticality=1.0,
        evidence_confidence=1.0,
    )
    decision = decide(debt_inputs=debt, candidate_regression=candidate_result())
    assert decision.metadata["scores"]["debt"]["principal"] == pytest.approx(0.17)
    assert decision.metadata["scores"]["debt"]["interest"] == pytest.approx(0.17)
    assert decision.debt_risk == pytest.approx(0.17)
    assert decision.autonomy_level >= 1
    priority = next(g for g in decision.rationale["gates"] if g["code"] == "debt_priority")
    assert priority["passed"] is True
    assert priority["observed"]["confirmed_candidate_regression"] is True
    assert decide(debt_inputs=debt, candidate_regression=None).autonomy_level == 0


def test_a3_requires_candidate_regression_evidence_even_with_high_debt_score():
    decision = decide(candidate_regression=None)
    assert decision.autonomy_level == 2
    assert decision.action == "generate_patch"
    gate = next(
        g for g in decision.rationale["gates"] if g["code"] == "candidate_regression_evidence"
    )
    assert gate["passed"] is False


def test_candidate_regression_from_other_run_declines_a3():
    decision = decide(candidate_regression=candidate_result(analysis_run_id="other-run"))
    assert decision.autonomy_level == 0
    assert decision.rationale["declined"] is True
    mismatch = next(
        reason
        for reason in decision.rationale["decline_reasons"]
        if reason["code"] == "candidate_regression_run"
    )
    assert mismatch["expected"] == "run-1"
    assert mismatch["actual"] == "other-run"


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "baseline"},
        {"status": "passed"},
        {"status": "inconclusive"},
    ],
)
def test_unconfirmed_candidate_result_cannot_enable_a3(changes):
    decision = decide(candidate_regression=candidate_result(**changes))
    assert decision.autonomy_level == 2
    gate = next(
        g for g in decision.rationale["gates"] if g["code"] == "candidate_regression_evidence"
    )
    assert gate["passed"] is False


def test_candidate_result_must_match_the_analysis_candidate_commit():
    decision = decide(candidate_regression=candidate_result(commit_sha="stale"))
    assert decision.autonomy_level == 0
    assert any(
        reason["code"] == "candidate_regression_commit"
        for reason in decision.rationale["decline_reasons"]
    )


@pytest.mark.parametrize(
    "failure",
    [
        OSError("git unavailable"),
        subprocess.TimeoutExpired(cmd="git", timeout=5),
    ],
)
def test_unavailable_git_is_distinct_from_failed_ancestry(failure):
    with mock_patch("lou.decision.autonomy.subprocess.run", side_effect=failure):
        decision = decide()
    assert decision.autonomy_level == 0
    codes = {reason["code"] for reason in decision.rationale["decline_reasons"]}
    assert "fix_commit_ancestry_unavailable" in codes
    assert "fix_commit_ancestry_failed" not in codes
    reason = next(
        reason
        for reason in decision.rationale["decline_reasons"]
        if reason["code"] == "fix_commit_ancestry_unavailable"
    )
    assert reason["reason"] == "Ancestry could not be determined."


def test_git_command_error_is_ancestry_unavailable():
    command_error = subprocess.CompletedProcess(args=["git"], returncode=128)
    with mock_patch("lou.decision.autonomy.subprocess.run", return_value=command_error):
        decision = decide()
    assert decision.autonomy_level == 0
    assert any(
        reason["code"] == "fix_commit_ancestry_unavailable"
        for reason in decision.rationale["decline_reasons"]
    )


@pytest.mark.parametrize(
    "feature,value,level",
    [
        ("blast_radius", 0.8, 1),
        ("criticality", 0.8, 1),
        ("patch_size", 0.8, 1),
        ("reversibility", 0.499999, 1),
        ("reversibility", 0.5, 2),
        ("reversibility", 0.799999, 2),
        ("reversibility", 0.8, 3),
        ("coverage", 0.799999, 2),
        ("coverage", 0.8, 3),
        ("verification_strength", 0.799999, 2),
        ("verification_strength", 0.8, 3),
        ("schema_migration_risk", 0.001, 1),
        ("data_migration_risk", 1.0, 1),
        ("context_completeness", 0.999999, 1),
        ("context_completeness", 0.0, 0),
    ],
)
def test_high_risk_and_protective_gates(feature, value, level):
    decision = decide(remediation_inputs=RemediationInputs(**(REMEDIATION | {feature: value})))
    assert decision.autonomy_level == level
    assert decision.rationale["gates"]


@pytest.mark.parametrize("risk,level", [(0.399999, 2), (0.4, 2), (0.400001, 1)])
def test_patch_risk_boundary(risk, level):
    # 0.15 coverage deficit + 0.15 weak verification + 0.10 patch size = 0.35.
    # Blast radius supplies the remaining risk while staying below its hard cap.
    values = REMEDIATION | {
        "blast_radius": (risk - 0.35) / 0.2,
        "criticality": 0.0,
        "coverage": 0.0,
        "verification_strength": 0.0,
        "patch_size": 0.5,
    }
    assert decide(remediation_inputs=RemediationInputs(**values)).autonomy_level == level


@pytest.mark.parametrize("risk,level", [(0.199999, 3), (0.2, 3), (0.200001, 2)])
def test_pr_risk_boundary(risk, level):
    values = REMEDIATION | {
        "blast_radius": (risk - 0.16) / 0.2,
        "criticality": 0.7,
        "patch_size": 0.4,
        "coverage": 0.9,
    }
    assert decide(remediation_inputs=RemediationInputs(**values)).autonomy_level == level


@pytest.mark.parametrize("feature", list(DEBT))
def test_any_missing_debt_input_cannot_enable_local_changes(feature):
    decision = decide(debt_inputs=DebtInputs(**(DEBT | {feature: None})))
    assert decision.autonomy_level <= 1
    assert decision.confidence < decide().confidence
    assert f"debt.{feature}" in decision.rationale["missing_inputs"]


@pytest.mark.parametrize("feature", list(REMEDIATION))
def test_any_missing_patch_input_cannot_enable_local_changes(feature):
    decision = decide(remediation_inputs=RemediationInputs(**(REMEDIATION | {feature: None})))
    assert decision.autonomy_level <= 1
    assert decision.confidence < decide().confidence
    assert f"remediation.{feature}" in decision.rationale["missing_inputs"]


@pytest.mark.parametrize("status,ceiling", product(["failed", "inconclusive"], range(4)))
def test_failed_or_inconclusive_never_allows_a3(status, ceiling):
    decision = decide(
        verification_results=[result(status=status)], policy=AutonomyPolicy(max_autonomy=ceiling)
    )
    assert decision.autonomy_level == min(2, ceiling)


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "baseline"},
        {"phase": "candidate"},
        {"analysis_run_id": "other-run"},
        {"commit_sha": "stale"},
        {"metadata": {}},
        {"metadata": {"patch_sha256": "other-patch"}},
        {"workload_id": "other-workload"},
        {"workload_id": None},
    ],
)
def test_unrelated_or_unbound_pass_cannot_enable_a3(changes):
    decision = decide(verification_results=[result(**changes)])
    expected = 0
    assert decision.autonomy_level == expected
    assert decision.rationale["declined"] is (expected == 0)
    if expected == 0:
        assert decision.rationale["decline_reasons"]


@pytest.mark.parametrize(
    "case,level",
    [
        ("no_patch", 2),
        ("wrong_patch_run", 0),
        ("empty_file_count", 2),
        ("empty_line_count", 2),
        ("no_expected_fix", 0),
        ("empty_expected_fix", 0),
        ("no_workload_plan", 0),
        ("empty_workload_id", 0),
        ("missing_workload_result", 0),
    ],
)
def test_missing_publication_context_declines_or_caps(case, level):
    changes = {
        "no_patch": {"patch": None},
        "wrong_patch_run": {"patch": PATCH.model_copy(update={"analysis_run_id": "other"})},
        "empty_file_count": {"patch": PATCH.model_copy(update={"files_changed": 0})},
        "empty_line_count": {
            "patch": PATCH.model_copy(update={"lines_added": 0, "lines_deleted": 0})
        },
        "no_expected_fix": {"expected_fix_commit_sha": None},
        "empty_expected_fix": {"expected_fix_commit_sha": ""},
        "no_workload_plan": {"required_workload_ids": []},
        "empty_workload_id": {"required_workload_ids": [""]},
        "missing_workload_result": {"required_workload_ids": ["checkout", "unit-tests"]},
    }
    decision = decide(**changes[case])
    assert decision.autonomy_level == level
    assert decision.rationale["declined"] is (level == 0)


@pytest.mark.parametrize("status,level", [("passed", 3), ("failed", 2), ("inconclusive", 2)])
def test_all_required_workloads_must_pass(status, level):
    results = [result(), result(verification_run_id="verify-2", workload_id="unit", status=status)]
    job = ANALYSIS_JOB.model_copy(update={"verification_plan": {"workloads": ["checkout", "unit"]}})
    assert (
        decide(
            required_workload_ids=["checkout", "unit"],
            verification_results=results,
            analysis_job=job,
        ).autonomy_level
        == level
    )
    # A passing result cannot hide another failure, including for the same workload.
    assert decide(verification_results=[result(), result(status=status)]).autonomy_level == level


@pytest.mark.parametrize("confidence,ceiling", product([0.0, 0.5, 0.75, 1.0], range(4)))
def test_policy_only_lowers_evidence_selected_level(confidence, ceiling):
    debt = DebtInputs(**(DEBT | {"evidence_confidence": confidence}))
    unrestricted = decide(debt_inputs=debt)
    restricted = decide(
        debt_inputs=debt, policy=AutonomyPolicy(max_autonomy=ceiling, revision="team-2")
    )
    assert restricted.autonomy_level == min(unrestricted.autonomy_level, ceiling)
    assert restricted.metadata["autonomy_before_policy"] == unrestricted.autonomy_level
    assert restricted.metadata["policy_revision"] == "team-2"


@pytest.mark.parametrize("invalid", [-1, 4, 1.5, True, "3"])
def test_invalid_policy_ceiling_is_rejected(invalid):
    with pytest.raises(ValidationError):
        AutonomyPolicy(max_autonomy=invalid)


def test_default_policy_stays_local():
    assert decide(policy=None).autonomy_level == 0
    assert decide(policy=AutonomyPolicy()).autonomy_level == 2
    for ceiling in range(4):
        assert (
            decide(policy=None).autonomy_level
            <= decide(policy=AutonomyPolicy(max_autonomy=ceiling)).autonomy_level
        )


def test_decision_explanations_are_structured_and_reproducible():
    decision = decide(verification_results=[result(status="failed")])
    assert (
        decide(verification_results=[result(status="failed")]).model_dump_json()
        == decision.model_dump_json()
    )
    assert decision.metadata["rule_revision"] == "autonomy-v1"
    assert decision.metadata["scores"]["debt"]["principal"] == pytest.approx(0.48)
    assert decision.metadata["scores"]["remediation"]["remediation_risk"] == pytest.approx(0.06)
    gate = next(g for g in decision.rationale["gates"] if g["code"] == "verification_passed")
    assert gate["observed"] == ["failed"]
    assert not gate["passed"]
    assert gate["reason"] in decision.rationale["reasons"]


def test_existing_shared_fixtures_can_feed_decisions_without_contract_changes():
    fixtures = Path(__file__).parents[3] / "contracts" / "fixtures"
    job = AnalysisJob.model_validate_json((fixtures / "analysis_job.json").read_text())
    finding = Finding.model_validate_json((fixtures / "finding.json").read_text())
    context = RepositoryContext.model_validate_json(
        (fixtures / "repository_context.json").read_text()
    )
    verification = VerificationResult.model_validate_json(
        (fixtures / "verification_result.json").read_text()
    )
    decision = decide_autonomy(
        decision_id="fixture-decision",
        analysis_run_id=job.analysis_run_id,
        debt_inputs=DebtInputs(**(DEBT | {"evidence_confidence": finding.confidence})),
        remediation_inputs=RemediationInputs(
            **(
                REMEDIATION
                | {
                    "evidence_confidence": finding.confidence,
                    "context_completeness": context.completeness,
                }
            )
        ),
        verification_results=[verification],
        policy=AutonomyPolicy(max_autonomy=3),
    )
    assert decision.autonomy_level == 1  # Fixture graph is only 95% complete.
    assert decision.confidence == pytest.approx(0.91 * 0.95)
    assert LouDecision.model_validate_json(decision.model_dump_json()) == decision


def test_randomized_identity_failures_decline_with_both_values():
    rng = Random(20260912)
    for _ in range(30):
        suffix = str(rng.getrandbits(64))
        cases = {
            "patch_content_hash": {"patch_content": PATCH_CONTENT + suffix.encode()},
            "analysis_run": {
                "analysis_job": ANALYSIS_JOB.model_copy(
                    update={"analysis_run_id": "other-" + suffix}
                )
            },
            "patch_base_commit": {
                "patch": PATCH.model_copy(update={"base_commit_sha": "other-" + suffix})
            },
            "fix_commit_ancestry_failed": {
                "expected_fix_commit_sha": FIX_UNRELATED_SHA,
                "verification_results": [result(commit_sha=FIX_UNRELATED_SHA)],
            },
            "workload_analysis_run": {
                "verification_results": [result(analysis_run_id="other-" + suffix)]
            },
            "workload_phase": {"verification_results": [result(phase="candidate")]},
            "workload_patch_hash": {
                "verification_results": [
                    result(
                        metadata={
                            "patch_sha256": "other-" + suffix,
                            "verification_attempt_id": "attempt-1",
                        }
                    )
                ]
            },
            "workload_verification_attempt": {
                "verification_results": [
                    result(
                        metadata={
                            "patch_sha256": PATCH.patch_sha256,
                            "verification_attempt_id": "other-" + suffix,
                        }
                    )
                ]
            },
            "workload_results": {"verification_results": [result(workload_id="other-" + suffix)]},
            "verification_plan_workloads": {
                "analysis_job": ANALYSIS_JOB.model_copy(
                    update={"verification_plan": {"workloads": None}}
                )
            },
        }
        for code, changes in cases.items():
            decision = decide(**changes)
            assert decision.autonomy_level == 0
            assert decision.action == "report"
            assert decision.rationale["declined"] is True
            failure = next(
                item for item in decision.rationale["decline_reasons"] if item["code"] == code
            )
            assert failure["expected"] != failure["actual"]


def test_removing_evidence_from_complete_randomized_inputs_never_raises_autonomy():
    rng = Random(2106)
    for sample in range(41):
        debt_values = DEBT if sample == 0 else DEBT | {"evidence_confidence": rng.random()}
        risk_values = (
            REMEDIATION
            if sample == 0
            else REMEDIATION
            | {
                "blast_radius": rng.uniform(0.0, 0.3),
                "criticality": rng.uniform(0.0, 0.3),
                "coverage": rng.uniform(0.8, 1.0),
                "verification_strength": rng.uniform(0.8, 1.0),
                "reversibility": rng.uniform(0.8, 1.0),
                "evidence_confidence": rng.random(),
            }
        )
        original = decide(
            debt_inputs=DebtInputs(**debt_values),
            remediation_inputs=RemediationInputs(**risk_values),
        ).autonomy_level
        if sample == 0:
            assert original == 3
        for name in debt_values:
            removed = decide(
                debt_inputs=DebtInputs(**(debt_values | {name: None})),
                remediation_inputs=RemediationInputs(**risk_values),
            )
            assert removed.autonomy_level <= original
        for name in risk_values:
            removed = decide(
                debt_inputs=DebtInputs(**debt_values),
                remediation_inputs=RemediationInputs(**(risk_values | {name: None})),
            )
            assert removed.autonomy_level <= original
        for changes in (
            {"remediation_inputs": None},
            {"patch": None},
            {"patch_content": None},
            {"analysis_job": None},
            {"expected_fix_commit_sha": None},
            {"expected_verification_attempt_id": None},
            {"required_workload_ids": []},
            {"verification_results": []},
            {"candidate_regression": None},
            {"policy": None},
        ):
            removed = decide(
                **(
                    {
                        "debt_inputs": DebtInputs(**debt_values),
                        "remediation_inputs": RemediationInputs(**risk_values),
                    }
                    | changes
                )
            )
            assert removed.autonomy_level <= original


def test_randomized_policy_ceiling_never_raises_evidence_level():
    rng = Random(36)
    for _ in range(100):
        confidence = rng.random()
        ceiling = rng.randrange(4)
        unrestricted = decide(
            debt_inputs=DebtInputs(**(DEBT | {"evidence_confidence": confidence}))
        ).autonomy_level
        restricted = decide(
            debt_inputs=DebtInputs(**(DEBT | {"evidence_confidence": confidence})),
            policy=AutonomyPolicy(max_autonomy=ceiling),
        ).autonomy_level
        assert restricted <= unrestricted
        assert restricted <= ceiling


def test_randomized_protective_features_never_raise_remediation_risk():
    rng = Random(72)
    for _ in range(300):
        base = REMEDIATION | {
            "blast_radius": rng.random(),
            "criticality": rng.random(),
            "patch_size": rng.random(),
            "schema_migration_risk": rng.random(),
            "data_migration_risk": rng.random(),
        }
        for name in ("coverage", "reversibility", "verification_strength"):
            lower, higher = sorted((rng.random(), rng.random()))
            low = score_remediation(RemediationInputs(**(base | {name: lower})))
            high = score_remediation(RemediationInputs(**(base | {name: higher})))
            assert high.remediation_risk <= low.remediation_risk


def test_decision_exposes_raw_and_normalized_signals_without_changing_score():
    missing_debt = DebtInputs(**(DEBT | {"churn": None}))
    decision = decide(debt_inputs=missing_debt)
    signals = decision.metadata["signals"]
    for name in (
        "complexity",
        "coverage_deficit",
        "churn",
        "graph_centrality",
        "runtime_impact",
        "path_criticality",
    ):
        assert name in signals["debt"]
        term = signals["debt"][name]
        assert term["raw_value"] == getattr(missing_debt, name)
        assert term["normalized_value"] == (getattr(missing_debt, name) or 0.0)
    assert signals["debt"]["churn"]["missing"] is True
    assert "debt.churn" in signals["missing_inputs"]
    assert "debt.churn" in decision.rationale["missing_inputs"]
    assert signals["remediation"]["coverage"]["raw_value"] == 0.9
    assert signals["remediation"]["coverage"]["normalized_value"] == 0.1
    assert LouDecision.model_validate_json(decision.model_dump_json()) == decision
