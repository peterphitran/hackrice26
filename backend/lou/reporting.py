"""Read-only, deterministic evidence reports for persisted Lou analysis runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    LouDecisionRecord,
    RepositoryRecord,
    VerificationRunRecord,
    WorkloadRecord,
)


class ReportError(Exception):
    """A safe, user-facing report-read failure."""


@dataclass(frozen=True)
class EvidenceReport:
    """A versioned, JSON-safe report with deterministic renderers."""

    payload: dict[str, object]

    def render_json(self) -> str:
        return json.dumps(self.payload, sort_keys=True, indent=2) + "\n"

    def render_markdown(self) -> str:
        run = _mapping(self.payload["run"])
        repository = _mapping(self.payload["repository"])
        context = _mapping(self.payload["context"])
        decision = self.payload.get("decision")
        lines = [
            "# Lou Evidence Report",
            "",
            f"- Run: `{run['analysis_run_id']}`",
            f"- Repository: `{repository['name']}`",
            f"- Status: `{run['status']}`",
            f"- Base → candidate: `{run['base_commit_sha']}` → `{run['candidate_commit_sha']}`",
            "",
            "## Selected Workloads",
            "",
        ]
        for workload in _list(self.payload["workloads"]):
            item = _mapping(workload)
            lines.append(f"- `{item['workload_id']}` ({item['workload_type']}): {item['reason']}")
        lines.extend(["", "## Measurements", ""])
        for verification in _list(self.payload["verification_runs"]):
            item = _mapping(verification)
            metrics = _mapping(item["metrics"])
            summary = ", ".join(f"{key}={value}" for key, value in sorted(metrics.items()))
            lines.append(
                f"- `{item['phase']}` / `{item['workload_id'] or 'aggregate'}`: "
                f"**{item['status']}**" + (f" — {summary}" if summary else "")
            )
        lines.extend(["", "## Findings", ""])
        findings = _list(self.payload["findings"])
        if findings:
            for finding in findings:
                item = _mapping(finding)
                lines.append(f"- **{item['severity']}** `{item['category']}`: {item['title']}")
        else:
            lines.append("- No findings were recorded.")
        lines.extend(["", "## Decision", ""])
        if decision is None:
            lines.append("- No decision was recorded.")
        else:
            item = _mapping(decision)
            lines.append(
                f"- Action: **{item['action']}**; confidence: `{item['confidence']}`; "
                f"autonomy level: `{item['autonomy_level']}`"
            )
        unresolved = _list(context.get("unresolved_relationships", []))
        if unresolved:
            lines.extend(["", "## Unresolved Relationships", ""])
            lines.extend(f"- {item}" for item in unresolved)
        return "\n".join(lines) + "\n"


class EvidenceReportReader:
    """Read persisted evidence without starting workloads or modifying the database."""

    def __init__(self, session_factory: sessionmaker[Session], artifact_root: Path) -> None:
        self._session_factory = session_factory
        self._artifact_root = artifact_root.resolve()

    def read(self, run_id: str) -> EvidenceReport:
        try:
            identifier = UUID(run_id)
        except ValueError as error:
            raise ReportError("run ID is invalid") from error
        with self._session_factory() as session:
            run = session.get(AnalysisRunRecord, identifier)
            if run is None:
                raise ReportError("analysis run was not found")
            repository = session.get(RepositoryRecord, run.repository_id)
            if repository is None:
                raise ReportError("analysis repository was not found")
            workloads = list(
                session.scalars(
                    select(WorkloadRecord)
                    .where(WorkloadRecord.repository_id == repository.id)
                    .order_by(WorkloadRecord.name)
                )
            )
            verification_runs = list(
                session.scalars(
                    select(VerificationRunRecord)
                    .where(VerificationRunRecord.analysis_run_id == run.id)
                    .order_by(VerificationRunRecord.phase, VerificationRunRecord.attempt)
                )
            )
            findings = list(
                session.scalars(
                    select(FindingRecord)
                    .where(FindingRecord.analysis_run_id == run.id)
                    .order_by(FindingRecord.phase, FindingRecord.fingerprint)
                )
            )
            evidence = list(
                session.scalars(
                    select(EvidenceRecord)
                    .where(EvidenceRecord.analysis_run_id == run.id)
                    .order_by(EvidenceRecord.phase, EvidenceRecord.contract_id)
                )
            )
            decision = session.scalar(
                select(LouDecisionRecord).where(LouDecisionRecord.analysis_run_id == run.id)
            )
        return EvidenceReport(
            {
                "schema_version": "1",
                "run": {
                    "analysis_run_id": str(run.id),
                    "status": run.status,
                    "base_commit_sha": run.base_commit_sha,
                    "candidate_commit_sha": run.candidate_commit_sha,
                    "fix_commit_sha": run.fix_commit_sha,
                    "toolchain_revision": run.toolchain_revision,
                    "policy_revision": run.policy_revision,
                },
                "repository": {"name": repository.repository_name, "provider": repository.provider},
                "context": _context(evidence),
                "workloads": [_workload(item) for item in workloads],
                "verification_runs": [_verification(item, workloads) for item in verification_runs],
                "findings": [_finding(item) for item in findings],
                "evidence": [_evidence(item, self._artifact_root) for item in evidence],
                "decision": _decision(decision),
            }
        )


def _context(evidence: list[EvidenceRecord]) -> dict[str, object]:
    record = next((item for item in evidence if item.kind == "repository-context"), None)
    if record is None:
        return {}
    value = record.summary.get("context", {})
    return value if isinstance(value, dict) else {}


def _workload(record: WorkloadRecord) -> dict[str, object]:
    selectors = record.selectors
    return {
        "workload_id": record.name,
        "workload_type": record.workload_type,
        "definition_path": record.definition_path,
        "reason": selectors.get("reason", "No selection reason recorded."),
        "confidence": selectors.get("confidence", 0),
    }


def _verification(
    record: VerificationRunRecord, workloads: list[WorkloadRecord]
) -> dict[str, object]:
    names = {item.id: item.name for item in workloads}
    return {
        "phase": record.phase,
        "attempt": record.attempt,
        "status": record.status,
        "commit_sha": record.commit_sha,
        "workload_id": names.get(record.workload_id) if record.workload_id is not None else None,
        "metrics": record.aggregate_metrics,
        "artifact": _artifact(record.artifact_uri, record.artifact_sha256, None),
    }


def _finding(record: FindingRecord) -> dict[str, object]:
    return {
        "category": record.category,
        "severity": record.severity,
        "confidence": record.confidence,
        "phase": record.phase,
        "title": record.title,
        "message": record.message,
        "file_path": record.file_path,
        "symbol_key": record.symbol_key,
    }


def _evidence(record: EvidenceRecord, artifact_root: Path) -> dict[str, object]:
    return {
        "evidence_id": record.contract_id,
        "phase": record.phase,
        "kind": record.kind,
        "source": record.source,
        "summary": record.summary,
        "artifact": _artifact(record.artifact_uri, record.artifact_sha256, artifact_root),
    }


def _decision(record: LouDecisionRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "decision_id": record.decision_id,
        "action": record.action,
        "debt_risk": record.debt_risk,
        "remediation_risk": record.remediation_risk,
        "confidence": record.confidence,
        "autonomy_level": record.autonomy_level,
        "rationale": record.rationale,
    }


def _artifact(
    uri: str | None, expected_hash: str | None, artifact_root: Path | None
) -> dict[str, object] | None:
    if uri is None and expected_hash is None:
        return None
    if uri is None or expected_hash is None:
        raise ReportError("artifact identity is incomplete")
    if artifact_root is None:
        return {"uri": uri, "sha256": expected_hash}
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ReportError("artifact URI is not a local file")
    # Earlier local adapters produced ``file://.lou/artifacts/...`` for a
    # relative artifact root.  In URI syntax that puts `.lou` in ``netloc``;
    # accept it as a local, relative path while still rejecting any path that
    # escapes the configured artifact root.
    raw_path = (
        f"{parsed.netloc}{unquote(parsed.path)}"
        if parsed.netloc and parsed.netloc != "localhost"
        else unquote(parsed.path)
    )
    path = Path(raw_path).resolve()
    root = artifact_root.resolve()
    if not path.is_relative_to(root):
        raise ReportError("artifact is outside the configured artifact root")
    try:
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ReportError("artifact is unavailable") from error
    if actual_hash != expected_hash:
        raise ReportError("artifact hash does not match persisted evidence")
    return {"path": str(path.relative_to(root)), "sha256": expected_hash}


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
