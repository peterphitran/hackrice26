"""A saved legacy decision cannot bypass the M5 PR boundary."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from contracts import (
    AgentResult,
    AutonomyDecision,
    ConfidenceBounds,
    DebtAssessment,
    ExpectedValue,
    LouDecision,
    PatchArtifact,
    PublicationResult,
    RemediationRisk,
    VerificationResult,
)
from lou.agents.orchestration import OrchestrationLimits, OrchestrationState
from lou.agents.patch_validation import ParsedPatchFile, PatchValidationResult
from lou.agents.provider import ProviderResponse
from lou.application.publication import GitHubPublisher, PublicationService
from lou.reporting import EvidenceReportReader


def _decision(run_id: str, *, m5: bool) -> LouDecision:
    metadata: dict[str, object] = {}
    if m5:
        debt = DebtAssessment(
            analysis_run_id=run_id,
            rule_revision="debt-v2",
            principal=0.5,
            interest=0.1,
            debt_risk=0.3,
            confidence=1,
            bounds=ConfidenceBounds(lower=0.3, upper=0.3),
            features=(),
            missing_inputs=(),
        )
        remediation = RemediationRisk(
            analysis_run_id=run_id,
            rule_revision="remediation-v2",
            risk=0.1,
            confidence=1,
            bounds=ConfidenceBounds(lower=0.1, upper=0.1),
            features=(),
            missing_inputs=(),
            hard_risk_flags=(),
        )
        record = AutonomyDecision(
            decision_id="decision-1",
            analysis_run_id=run_id,
            rule_revision="autonomy-v2",
            requested_policy_revision="1",
            evaluated_policy_revision="1",
            evidence_level=3,
            organization_ceiling=3,
            permitted_level=3,
            action="open_pr",
            denied_reasons=(),
            debt=debt,
            remediation=remediation,
            expected_value=ExpectedValue(
                status="estimated",
                horizon_days=30,
                central_hours=30,
                lower_hours=20,
                upper_hours=40,
            ),
        )
        metadata["m5"] = record.model_dump(mode="json")
    return LouDecision(
        decision_id="decision-1",
        analysis_run_id=run_id,
        debt_risk=0.3,
        remediation_risk=0.1,
        confidence=1,
        autonomy_level=3,
        action="open_pr",
        metadata=metadata,
    )


def _service(*, m5: bool, policy_revision: str = "1") -> tuple[PublicationService, str]:
    analysis_run_id = uuid4()
    run_id = str(analysis_run_id)
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    patch_hash = sha256(diff.encode()).hexdigest()
    patch = PatchArtifact(
        patch_id="patch-1",
        analysis_run_id=run_id,
        base_commit_sha="a" * 40,
        patch_sha256=patch_hash,
        artifact_uri="memory://patch",
        files_changed=1,
        lines_added=1,
        lines_deleted=1,
    )
    response = ProviderResponse(
        result=AgentResult(agent_run_id="agent-1", analysis_run_id=run_id, status="succeeded"),
        patch_diff=diff,
        patch_artifact=patch,
        timeout_seconds=1,
        retry_count=0,
        tokens_used=1,
        estimated_cost_usd=0,
        elapsed_ms=1,
    )
    state = OrchestrationState(
        analysis_run_id=run_id,
        inputs_fingerprint="f" * 64,
        limits=OrchestrationLimits(),
        started_at_epoch=0,
        stage="stopped",
        termination_reason="verified",
        current_patch_response=response,
        current_validation=PatchValidationResult(
            valid=True,
            reasons=[],
            files=[
                ParsedPatchFile(
                    path="app.py",
                    old_path="app.py",
                    new_path="app.py",
                    lines_added=1,
                    lines_deleted=1,
                    hunk_count=1,
                    binary=False,
                )
            ],
        ),
        current_verification_results=[
            VerificationResult(
                verification_run_id="fix-1",
                analysis_run_id=run_id,
                phase="fix",
                commit_sha="b" * 40,
                status="passed",
                workload_id="tests",
                metadata={"patch_sha256": patch_hash},
            )
        ],
        decision=_decision(run_id, m5=m5),
    )
    run = SimpleNamespace(
        analysis_run_id=analysis_run_id,
        policy_revision=policy_revision,
        status="succeeded",
        snapshot=state.model_dump(mode="json"),
    )
    analysis = SimpleNamespace(repository_id=uuid4(), policy_revision="1")
    repository = SimpleNamespace(owner_name="owner", repository_name="repo", default_branch="main")
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = [run, analysis]
    session.scalar.return_value = repository
    factory = MagicMock(return_value=session)
    reader = MagicMock()
    reader.read.return_value.render_json.return_value = "{}"
    service = PublicationService(
        cast(sessionmaker[Session], factory), cast(EvidenceReportReader, reader)
    )
    return service, run_id


def test_publisher_requires_m5_even_for_verified_legacy_a3() -> None:
    service, _ = _service(m5=False)
    _, _, dry_run_allowed, publish_allowed, reasons = service._plan(uuid4())
    assert dry_run_allowed
    assert not publish_allowed
    assert reasons == ("m5_decision_missing_or_invalid",)


def test_publish_never_calls_publisher_for_legacy_a3() -> None:
    service, _ = _service(m5=False)
    publisher = MagicMock()
    denied = PublicationResult(
        publication_plan_id="plan-1", agent_run_id="agent-1", status="denied"
    )
    with mock_patch.object(PublicationService, "_save", return_value=denied):
        result = service.publish(
            uuid4(),
            acknowledgement="PUBLISH_VERIFIED_REMEDIATION",
            publisher=cast(GitHubPublisher, publisher),
        )
    assert result.status == "denied"
    publisher.publish.assert_not_called()


def test_publisher_accepts_matching_m5_and_rejects_revision_change() -> None:
    service, _ = _service(m5=True)
    _, _, _, publish_allowed, reasons = service._plan(uuid4())
    assert publish_allowed
    assert reasons == ()

    changed, _ = _service(m5=True, policy_revision="outdated")
    _, _, _, publish_allowed, reasons = changed._plan(uuid4())
    assert not publish_allowed
    assert "policy_revision_mismatch" in reasons
