from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from contracts import (
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.application.analysis import AnalysisRequest, VerificationBundle
from lou.application.persistence import SqlAlchemyAnalysisStore
from lou.persistence.models import (
    AnalysisRunRecord,
    LouDecisionRecord,
    VerificationRunRecord,
    WorkloadRecord,
)

pytestmark = pytest.mark.integration


def test_application_store_persists_and_reuses_a_completed_run(
    session_factory: Any, tmp_path: Path
) -> None:
    (tmp_path / "loadtests").mkdir()
    (tmp_path / "loadtests" / "checkout.js").write_text("export default function() {}\n")
    request = AnalysisRequest("repo-contract", tmp_path, "a" * 40, "b" * 40)
    store = SqlAlchemyAnalysisStore(session_factory)

    created = store.create_or_get(request, request.deduplication_key())
    assert created.created is True
    assert created.status == "running"
    run_id = created.analysis_run_id
    selection = WorkloadSelection(
        workload_id="checkout-load",
        workload_type="k6",
        definition_path="loadtests/checkout.js",
        phase="candidate",
        reason="fixture workload",
        confidence=1.0,
    )
    store.record_context(
        run_id,
        RepositoryChange(
            repository_id="repo-contract",
            base_commit_sha="a" * 40,
            candidate_commit_sha="b" * 40,
        ),
        RepositoryContext(repository_id="repo-contract", commit_sha="b" * 40),
        (selection,),
    )
    store.record_verification(
        run_id,
        VerificationBundle(
            VerificationResult(
                verification_run_id="verify-contract-1",
                analysis_run_id=run_id,
                phase="candidate",
                commit_sha="b" * 40,
                status="failed",
                workload_id="checkout-load",
                metrics={"query_count_delta": 49.0},
            )
        ),
    )
    store.record_decision(
        run_id,
        LouDecision(
            decision_id="decision-contract-1",
            analysis_run_id=run_id,
            debt_risk=0.5,
            remediation_risk=None,
            confidence=0.5,
            autonomy_level=0,
            action="report",
            rationale={"summary": "verified regression; incomplete remediation inputs"},
        ),
    )
    store.finish(run_id, "succeeded")

    reused = store.create_or_get(request, request.deduplication_key())
    assert reused.created is False
    assert reused.status == "succeeded"
    assert reused.analysis_run_id == run_id

    with session_factory() as session:
        run = session.get(AnalysisRunRecord, UUID(run_id))
        workload = session.scalar(
            select(WorkloadRecord).where(WorkloadRecord.name == "checkout-load")
        )
        verification = session.scalar(
            select(VerificationRunRecord).where(
                VerificationRunRecord.contract_id == "verify-contract-1"
            )
        )
        decision = session.scalar(
            select(LouDecisionRecord).where(LouDecisionRecord.decision_id == "decision-contract-1")
        )
        assert run is not None and run.status == "succeeded"
        assert workload is not None
        assert verification is not None and verification.analysis_run_id == run.id
        assert decision is not None and decision.analysis_run_id == run.id
