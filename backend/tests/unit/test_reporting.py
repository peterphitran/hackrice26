"""Tests for deterministic, read-only evidence report rendering."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    LouDecisionRecord,
    RepositoryRecord,
    VerificationRunRecord,
    WorkloadRecord,
)
from lou.reporting import EvidenceReportReader, ReportError


class _Session:
    def __init__(self, records: dict[object, object], rows: dict[object, list[object]]) -> None:
        self.records = records
        self.rows = rows

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, model: object, _: object) -> object | None:
        return self.records.get(model)

    def scalars(self, statement: object) -> list[object]:
        entity = cast(Any, statement).column_descriptions[0]["entity"]
        return self.rows.get(entity, [])

    def scalar(self, statement: object) -> object | None:
        entity = cast(Any, statement).column_descriptions[0]["entity"]
        values = self.rows.get(entity, [])
        return values[0] if values else None


def test_report_reads_persisted_records_and_verifies_relative_artifacts(tmp_path: Path) -> None:
    run_id = uuid4()
    repository_id = uuid4()
    workload_id = uuid4()
    artifact = tmp_path / "run-1" / "comparison.json"
    artifact.parent.mkdir()
    artifact.write_text('{"query_count_delta":49}\n', encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    records: dict[object, object] = {
        AnalysisRunRecord: SimpleNamespace(
            id=run_id,
            repository_id=repository_id,
            status="succeeded",
            base_commit_sha="a" * 40,
            candidate_commit_sha="b" * 40,
            fix_commit_sha=None,
            toolchain_revision="1",
            policy_revision="1",
        ),
        RepositoryRecord: SimpleNamespace(
            id=repository_id, repository_name="broken-store", provider="local"
        ),
    }
    context = SimpleNamespace(
        contract_id="context-run-1",
        phase="comparison",
        kind="repository-context",
        source="lou.application",
        summary={"context": {"selected_workload_ids": ["checkout-load"]}},
        artifact_uri=None,
        artifact_sha256=None,
    )
    report_evidence = SimpleNamespace(
        contract_id="evidence-candidate",
        phase="comparison",
        kind="verification-comparison",
        source="lou.verification",
        summary={"query_count_delta": 49},
        artifact_uri=f"file://{artifact}",
        artifact_sha256=digest,
    )
    runtime_evidence = SimpleNamespace(
        contract_id="telemetry-candidate",
        phase="candidate",
        kind="runtime-observation",
        source="lou.telemetry",
        summary={
            "span_name": "lou.db.query",
            "exporter_status": "healthy",
            "workload_id": "checkout-load",
            "symbol_key": "store.app.Store.checkout",
            "attributes": {"db.operation": "SELECT", "db.table": "products"},
        },
        artifact_uri=None,
        artifact_sha256=None,
    )
    rows: dict[object, list[object]] = {
        WorkloadRecord: [
            SimpleNamespace(
                id=workload_id,
                name="checkout-load",
                workload_type="k6",
                definition_path="loadtests/checkout.js",
                selectors={"reason": "Endpoint is validated by checkout load.", "confidence": 0.9},
            )
        ],
        VerificationRunRecord: [
            SimpleNamespace(
                phase="candidate",
                attempt=1,
                status="failed",
                commit_sha="b" * 40,
                workload_id=workload_id,
                aggregate_metrics={"query_count_delta": 49},
                artifact_uri=None,
                artifact_sha256=None,
            )
        ],
        FindingRecord: [
            SimpleNamespace(
                category="database-query-regression",
                severity="high",
                confidence=0.91,
                phase="candidate",
                title="Query count increased",
                message="The candidate makes one query per product.",
                file_path="store/app.py",
                symbol_key="store.app.Store.checkout",
            )
        ],
        EvidenceRecord: [context, report_evidence, runtime_evidence],
        LouDecisionRecord: [
            SimpleNamespace(
                decision_id="decision-run-1",
                action="report",
                debt_risk=0.8,
                remediation_risk=None,
                confidence=0.91,
                autonomy_level=0,
                rationale={"outcome": "runtime_regression"},
            )
        ],
    }
    session = _Session(records, rows)
    report = EvidenceReportReader(cast(Any, lambda: session), tmp_path).read(str(run_id))

    first = report.render_json()
    assert first == report.render_json()
    assert '"query_count_delta": 49' in first
    assert '"path": "run-1/comparison.json"' in first
    assert "# Lou Evidence Report" in report.render_markdown()
    assert "Query count increased" in report.render_markdown()
    assert "## Runtime Correlation" in report.render_markdown()
    assert "store.app.Store.checkout" in report.render_markdown()


def test_report_rejects_missing_or_tampered_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="artifact"):
        from lou.reporting import _artifact

        _artifact(f"file://{tmp_path / 'missing.json'}", "0" * 64, tmp_path)


def test_report_accepts_legacy_relative_file_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lou.reporting import _artifact

    monkeypatch.chdir(tmp_path)
    artifact = tmp_path / ".lou/artifacts/run-1/comparison.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("proof", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert _artifact(
        "file://.lou/artifacts/run-1/comparison.json", digest, Path(".lou/artifacts")
    ) == {"path": "run-1/comparison.json", "sha256": digest}
