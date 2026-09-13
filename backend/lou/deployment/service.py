"""Append-only local evidence store and staging deployment orchestration."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from contracts import (
    CanaryObservation,
    CanaryWindow,
    DeploymentDecision,
    DeploymentEvidence,
    Release,
    SLOPolicy,
)
from lou.deployment.policy import evaluate_canary
from lou.deployment.ports import DeploymentPort, TraceLookupPort, VerificationLookupPort

DeploymentStatus = Literal["released", "paused", "promoted", "rolled_back"]
Clock = Callable[[], datetime]
_CHAIN_FIELDS = frozenset({"previous_hash", "record_hash"})


class DeploymentConflictError(ValueError):
    """A release ID was reused for a different immutable release."""


@dataclass(frozen=True)
class DeploymentResult:
    release: Release
    status: DeploymentStatus
    reused: bool
    decision: DeploymentDecision | None
    evidence: tuple[DeploymentEvidence, ...]


class DeploymentJournalPort(Protocol):
    """The append-only evidence store a deployment reads and writes."""

    def get(self, release_id: str) -> DeploymentResult | None: ...

    def create(self, release: Release) -> DeploymentResult: ...

    def append(self, evidence: DeploymentEvidence) -> DeploymentResult: ...


class ChainedJournal(ABC):
    """Hash-chained append-only journal semantics over any record store.

    Subclasses supply only storage. Keeping the chain here means the file and database
    backends cannot drift on the property that makes the evidence trustworthy.
    """

    def get(self, release_id: str) -> DeploymentResult | None:
        records = self._read(release_id)
        if not records:
            return None
        if records[0].get("kind") != "release":
            raise DeploymentConflictError("deployment journal is invalid")
        self._verify_chain(records)
        release = Release.model_validate(records[0]["release"])
        evidence = tuple(
            DeploymentEvidence.model_validate(record["evidence"])
            for record in records[1:]
            if record.get("kind") == "evidence"
        )
        return DeploymentResult(
            release=release,
            status=_status(evidence),
            reused=True,
            decision=next((item.decision for item in reversed(evidence) if item.decision), None),
            evidence=evidence,
        )

    def create(self, release: Release) -> DeploymentResult:
        existing = self.get(release.release_id)
        if existing is not None:
            if existing.release != release:
                raise DeploymentConflictError(
                    "release ID already belongs to different release inputs"
                )
            return existing
        self._append(
            release.release_id, {"kind": "release", "release": release.model_dump(mode="json")}
        )
        return DeploymentResult(release, "released", False, None, ())

    def append(self, evidence: DeploymentEvidence) -> DeploymentResult:
        current = self.get(evidence.release_id)
        if current is None:
            raise DeploymentConflictError("release does not exist")
        if (
            evidence.analysis_run_id != current.release.analysis_run_id
            or evidence.commit_sha != current.release.commit_sha
        ):
            raise DeploymentConflictError("deployment evidence does not match the release")
        if any(item.evidence_id == evidence.evidence_id for item in current.evidence):
            return current
        self._append(
            evidence.release_id, {"kind": "evidence", "evidence": evidence.model_dump(mode="json")}
        )
        result = self.get(evidence.release_id)
        assert result is not None
        return result

    @staticmethod
    def _digest(previous_hash: str, body: dict[str, object]) -> str:
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return sha256(f"{previous_hash}\x1f{payload}".encode()).hexdigest()

    @staticmethod
    def body_of(record: dict[str, object]) -> dict[str, object]:
        """Return the chained payload: everything the record hash is computed over."""

        return {key: value for key, value in record.items() if key not in _CHAIN_FIELDS}

    def _verify_chain(self, records: list[dict[str, object]]) -> None:
        """Reject a journal whose records were edited, removed, or appended out of band."""

        previous_hash = ""
        for record in records:
            body = self.body_of(record)
            if record.get("previous_hash") != previous_hash:
                raise DeploymentConflictError("deployment journal chain is broken")
            expected = self._digest(previous_hash, body)
            if record.get("record_hash") != expected:
                raise DeploymentConflictError("deployment journal record was modified")
            previous_hash = expected

    def _append(self, release_id: str, value: dict[str, object]) -> None:
        records = self._read(release_id)
        previous_hash = str(records[-1].get("record_hash", "")) if records else ""
        self._write(
            release_id,
            len(records),
            {
                **value,
                "previous_hash": previous_hash,
                "record_hash": self._digest(previous_hash, value),
            },
        )

    @abstractmethod
    def _read(self, release_id: str) -> list[dict[str, object]]:
        """Return every stored record for the release, in chain order."""

    @abstractmethod
    def _write(self, release_id: str, sequence: int, record: dict[str, object]) -> None:
        """Append one record; reject a sequence another writer already claimed."""


class DeploymentJournal(ChainedJournal):
    """A local JSONL journal suitable for rehearsal and tests, not shared staging."""

    def __init__(self, artifact_root: Path) -> None:
        self._root = artifact_root / "deployments"

    def _read(self, release_id: str) -> list[dict[str, object]]:
        path = self._path(release_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def _write(self, release_id: str, sequence: int, record: dict[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(release_id)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def _path(self, release_id: str) -> Path:
        if not release_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("release ID contains unsupported characters")
        return self._root / f"{release_id}.jsonl"


class DeploymentService:
    """Coordinates a staging release without embedding controller credentials."""

    def __init__(
        self,
        journal: DeploymentJournalPort,
        adapter: DeploymentPort,
        clock: Clock | None = None,
        verifications: VerificationLookupPort | None = None,
        traces: TraceLookupPort | None = None,
    ) -> None:
        self._journal = journal
        self._adapter = adapter
        self._clock = clock or (lambda: datetime.now(UTC))
        self._verifications = verifications
        self._traces = traces

    def _confirmed_verifications(
        self, release: Release, verification_run_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Keep only IDs that name a passing verification run for this exact release.

        Without a lookup nothing can be confirmed, so a service configured without one
        can pause but never promote.
        """

        if self._verifications is None:
            return ()
        confirmed = []
        for verification_run_id in verification_run_ids:
            fact = self._verifications.get(verification_run_id)
            if (
                fact is not None
                and fact.status == "passed"
                and fact.analysis_run_id == release.analysis_run_id
                and fact.commit_sha == release.commit_sha
            ):
                confirmed.append(verification_run_id)
        return tuple(confirmed)

    def _confirmed_traces(self, release: Release, trace_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Keep only IDs that name a trace this release's own run actually recorded.

        Without a lookup nothing can be confirmed, so a service configured without one
        can pause but never promote.
        """

        if self._traces is None:
            return ()
        confirmed = []
        for trace_id in trace_ids:
            fact = self._traces.get(trace_id)
            if (
                fact is not None
                and fact.observation_count > 0
                and fact.analysis_run_id == release.analysis_run_id
                and fact.commit_sha == release.commit_sha
            ):
                confirmed.append(trace_id)
        return tuple(confirmed)

    def release(self, release: Release) -> DeploymentResult:
        if release.target.environment != "staging":
            raise ValueError("M8 accepts staging releases only")
        result = self._journal.create(release)
        if result.reused and any(item.event == "released" for item in result.evidence):
            return result
        # A journal with no released event means the prior adapter call failed.
        # Retry the idempotent adapter operation instead of stranding the release.
        self._adapter.release(release)
        persisted = self._journal.append(self._evidence(release, "released", release.requested_by))
        return DeploymentResult(
            persisted.release,
            persisted.status,
            False,
            persisted.decision,
            persisted.evidence,
        )

    def observe(
        self,
        *,
        release_id: str,
        window: CanaryWindow,
        observation: CanaryObservation,
        policy: SLOPolicy,
        verification_run_ids: tuple[str, ...] = (),
        trace_ids: tuple[str, ...] = (),
    ) -> DeploymentResult:
        current = self._require(release_id)
        if current.status in {"promoted", "rolled_back"}:
            return current
        if window.release_id != release_id or observation.release_id != release_id:
            raise DeploymentConflictError("canary data does not belong to the release")
        release = current.release
        confirmed = self._confirmed_verifications(release, verification_run_ids)
        confirmed_traces = self._confirmed_traces(release, trace_ids)
        self._journal.append(
            self._evidence(
                release,
                "validated",
                "lou-validation",
                verification_run_ids=confirmed,
                metadata={
                    "validation_passed": observation.validation_passed,
                    "verification_runs_claimed": len(verification_run_ids),
                    "verification_runs_confirmed": len(confirmed),
                },
            )
        )
        decision = evaluate_canary(
            window=window,
            observation=observation,
            policy=policy,
            now=observation.observed_at,
            verification_run_ids=confirmed,
            trace_ids=confirmed_traces,
        )
        # Record the intended transition before touching the controller: a crash between
        # the two leaves the decision auditable, and the adapter call is idempotent.
        self._journal.append(
            self._evidence(
                release,
                "observed",
                "lou-observer",
                trace_ids=confirmed_traces,
                decision=decision,
                metadata={
                    "sample_count": observation.sample_count,
                    "telemetry_available": observation.telemetry_available,
                    "validation_passed": observation.validation_passed,
                    "traces_claimed": len(trace_ids),
                    "traces_confirmed": len(confirmed_traces),
                },
            )
        )
        if decision.action == "promote":
            self._adapter.promote(release)
            event: Literal["promoted", "paused", "rolled_back"] = "promoted"
        elif decision.action == "rollback":
            self._adapter.rollback(release)
            event = "rolled_back"
        else:
            self._adapter.pause(release)
            event = "paused"
        return self._journal.append(self._evidence(release, event, "lou-policy", decision=decision))

    def rollback(self, release_id: str, *, actor: str, reason: str) -> DeploymentResult:
        current = self._require(release_id)
        if current.status == "rolled_back":
            return current
        decision = DeploymentDecision(
            release_id=release_id,
            action="rollback",
            policy_revision="manual-v1",
            reasons=(reason,),
            decided_at=self._clock(),
        )
        self._adapter.rollback(current.release)
        return self._journal.append(
            self._evidence(current.release, "rolled_back", actor, decision=decision)
        )

    def status(self, release_id: str) -> DeploymentResult | None:
        return self._journal.get(release_id)

    def report(self, release_id: str) -> dict[str, object] | None:
        """Return the canonical, read-only evidence view for one deployment."""

        result = self.status(release_id)
        if result is None:
            return None
        return {
            "schema_version": "1",
            "release": result.release.model_dump(mode="json"),
            "status": result.status,
            "decision": result.decision.model_dump(mode="json") if result.decision else None,
            "evidence": [item.model_dump(mode="json") for item in result.evidence],
        }

    def _require(self, release_id: str) -> DeploymentResult:
        result = self._journal.get(release_id)
        if result is None:
            raise DeploymentConflictError("release was not found")
        return result

    def _evidence(
        self,
        release: Release,
        event: Literal["released", "validated", "observed", "promoted", "paused", "rolled_back"],
        actor: str,
        *,
        decision: DeploymentDecision | None = None,
        verification_run_ids: tuple[str, ...] = (),
        trace_ids: tuple[str, ...] = (),
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> DeploymentEvidence:
        return DeploymentEvidence(
            evidence_id=f"deployment_{uuid4().hex}",
            release_id=release.release_id,
            analysis_run_id=release.analysis_run_id,
            commit_sha=release.commit_sha,
            event=event,
            actor=actor,
            collected_at=self._clock(),
            decision=decision,
            verification_run_ids=verification_run_ids,
            trace_ids=trace_ids,
            metadata=metadata or {},
        )


def _status(evidence: tuple[DeploymentEvidence, ...]) -> DeploymentStatus:
    events = {item.event for item in evidence}
    if "rolled_back" in events:
        return "rolled_back"
    if "promoted" in events:
        return "promoted"
    if "paused" in events:
        return "paused"
    return "released"
