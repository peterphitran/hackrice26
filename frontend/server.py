"""Local demo server for Lou.

Two surfaces. ``/api/demo`` replays the recorded decision scenarios from
scripts.demo_slice. ``/api/analyze`` runs Lou's real repository intelligence over a
repository you point it at: diff, symbols, graph, traversal, workload planning and
impact prediction all execute for real, with no database and no Docker.

Verification and the autonomy decision are deliberately absent from the live path.
Measuring a baseline and candidate needs the Docker sandbox, so this reports what it
observed and stops rather than implying a verdict it never measured.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from lou.core.version import APP_VERSION

PAGE = Path(__file__).parent / "index.html"
ARTIFACTS = Path(__file__).parent / ".artifacts"

CASE_LABELS: dict[str, dict[str, str]] = {
    "success": {
        "title": "Verified fix",
        "blurb": "The patch applies, both workloads pass, and every identity check binds.",
    },
    "delete-test": {
        "title": "Patch deletes a test",
        "blurb": "The agent removes the failing test instead of fixing it. Validation rejects "
        "each attempt until the attempt budget runs out.",
    },
    "wrong-hash": {
        "title": "Verdict does not match the patch",
        "blurb": "The verifier reports on a different patch hash than the one proposed, so the "
        "evidence cannot be bound to this change.",
    },
}


def _case(name: str, state: Any) -> dict[str, Any]:
    decision = state.decision
    rationale = dict(decision.rationale)
    return {
        "id": name,
        "title": CASE_LABELS.get(name, {}).get("title", name),
        "blurb": CASE_LABELS.get(name, {}).get("blurb", ""),
        "action": decision.action,
        "autonomy_level": decision.autonomy_level,
        "debt_risk": decision.debt_risk,
        "remediation_risk": decision.remediation_risk,
        "confidence": decision.confidence,
        "termination_reason": state.termination_reason,
        "attempt_count": state.attempt_count,
        "summary": rationale.get("summary", ""),
        "reasons": rationale.get("reasons", []),
        "gates": rationale.get("gates", []),
        "attempt_outcomes": [item.model_dump(mode="json") for item in state.attempt_outcomes],
        "verification_results": [
            {"workload_id": item.workload_id, "status": item.status, "phase": item.phase}
            for item in state.verification_results
        ],
    }


@lru_cache(maxsize=1)
def _demo() -> dict[str, Any]:
    # Seeding the scenario repositories takes a moment; the result is deterministic,
    # so it is computed once and reused for the life of the process.
    from scripts.demo_slice import run_demo

    transcript = io.StringIO()
    states = run_demo(transcript)
    return {
        "version": APP_VERSION,
        "cases": [_case(name, state) for name, state in states.items()],
        "transcript": transcript.getvalue().splitlines(),
    }


def _runs(repository_path: str | None = None) -> list[dict[str, Any]]:
    """Return persisted analysis runs with their verification, decision and loop.

    This reads what previous runs recorded rather than re-running anything. A
    remediation is minutes of sandboxed work, so the page shows the evidence it
    left behind instead of pretending to reproduce it on page load.
    """

    from sqlalchemy import select

    from lou.core.settings import get_settings
    from lou.persistence.database import create_session_factory
    from lou.persistence.models import (
        AgentRunRecord,
        AnalysisRunRecord,
        LouDecisionRecord,
        RemediationAttemptRecord,
        RepositoryRecord,
        VerificationRunRecord,
    )

    sessions = create_session_factory(get_settings())
    out: list[dict[str, Any]] = []
    with sessions() as session:
        statement = (
            select(AnalysisRunRecord, RepositoryRecord)
            .join(RepositoryRecord, RepositoryRecord.id == AnalysisRunRecord.repository_id)
            .order_by(AnalysisRunRecord.created_at.desc())
            .limit(25)
        )
        if repository_path:
            # Resolve so a path typed with a symlink or trailing slash still matches
            # the absolute path the analysis recorded.
            wanted = str(Path(repository_path).resolve())
            statement = statement.where(RepositoryRecord.local_path.in_((wanted, repository_path)))
        rows = session.execute(statement).all()
        for run, repository in rows:
            verifications = session.scalars(
                select(VerificationRunRecord)
                .where(VerificationRunRecord.analysis_run_id == run.id)
                .order_by(VerificationRunRecord.created_at)
            ).all()
            decision = session.scalars(
                select(LouDecisionRecord)
                .where(LouDecisionRecord.analysis_run_id == run.id)
                .order_by(LouDecisionRecord.created_at.desc())
            ).first()
            agent = session.scalars(
                select(AgentRunRecord)
                .where(AgentRunRecord.analysis_run_id == run.id)
                .order_by(AgentRunRecord.created_at.desc())
            ).first()
            attempts = (
                session.scalars(
                    select(RemediationAttemptRecord)
                    .where(RemediationAttemptRecord.agent_run_id == agent.id)
                    .order_by(RemediationAttemptRecord.created_at)
                ).all()
                if agent is not None
                else []
            )
            out.append(
                {
                    "id": str(run.id),
                    "status": run.status,
                    "repository": {
                        "name": repository.repository_name,
                        "path": repository.local_path,
                    },
                    "base": run.base_commit_sha,
                    "candidate": run.candidate_commit_sha,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "verifications": [
                        {
                            "phase": v.phase,
                            "status": v.status,
                            "metrics": v.aggregate_metrics,
                        }
                        for v in verifications
                    ],
                    "decision": (
                        {
                            "action": decision.action,
                            "autonomy_level": decision.autonomy_level,
                            "debt_risk": decision.debt_risk,
                            "confidence": decision.confidence,
                            "gates": (decision.rationale or {}).get("gates", []),
                            "summary": (decision.rationale or {}).get("summary", ""),
                            "reasons": (decision.rationale or {}).get("reasons", []),
                        }
                        if decision is not None
                        else None
                    ),
                    "remediation": (
                        {
                            "agent_run_id": str(agent.id),
                            "status": agent.status,
                            "stage": agent.stage,
                            "termination_reason": agent.termination_reason,
                            "attempts": [
                                {
                                    "attempt": a.attempt_number,
                                    "stage": a.stage,
                                    "outcome": a.outcome,
                                    "patch_sha256": a.patch_sha256,
                                }
                                for a in attempts
                            ],
                        }
                        if agent is not None
                        else None
                    ),
                }
            )
    return out


class AnalyzeRequest(BaseModel):
    repository_path: str = Field(min_length=1, max_length=4096)
    base: str = Field(min_length=1, max_length=256)
    candidate: str = Field(min_length=1, max_length=256)


STAGES: tuple[tuple[str, str], ...] = (
    ("revisions", "Resolve revisions"),
    ("diff", "Parse the diff"),
    ("symbols", "Extract changed symbols"),
    ("graph", "Build the repository graph"),
    ("traversal", "Traverse impact"),
    ("registry", "Load declared workloads"),
    ("plan", "Plan validation"),
    ("prediction", "Predict impact"),
    ("correlation", "Correlate runtime names"),
)


def _knowledge_graph(snapshot: Any, traversal: Any) -> dict[str, Any]:
    """Return the impact subgraph: the nodes the change reached and the edges between them.

    This is the traversal's induced subgraph, not the whole repository. The full graph
    is mostly unrelated to any one change, and the traversal is already bounded, so
    this shows what the change actually touches and how it got there.
    """

    reached = {node.node_id: node for node in traversal.nodes}
    nodes = [
        {
            "id": node_id,
            "type": node.node_type,
            "key": node.key,
            "label": node.key.rsplit(".", 1)[-1].rsplit("::", 1)[-1][:40] or node.key[:40],
            "path": node.path,
            "distance": node.distance,
            "confidence": node.confidence,
            "changed": node.distance == 0,
        }
        for node_id, node in reached.items()
    ]
    edges = [
        {"source": source, "target": target, "type": data.get("edge_type", "")}
        for source, target, data in snapshot.graph.edges(data=True)
        if source in reached and target in reached
    ]
    # Several parallel edges of the same type between two nodes add nothing to read.
    unique = {(e["source"], e["target"], e["type"]): e for e in edges}
    return {
        "nodes": sorted(nodes, key=lambda n: (n["distance"], n["type"], n["key"])),
        "edges": list(unique.values()),
        "max_distance": max((n["distance"] for n in nodes), default=0),
    }


def _stream(payload: AnalyzeRequest) -> Iterator[str]:
    """Run the analysis one stage at a time, reporting each as it finishes.

    The stages mirror FixtureRepositoryIntelligence.inspect. They are driven here
    rather than delegated so the page can show progress and partial evidence while
    the graph is still being built.
    """

    from lou.application.analysis import AnalysisRequest
    from lou.prediction import predict_impact
    from lou.repository import (
        build_repository_context,
        build_repository_graph,
        load_workload_registry,
        parse_repository_changes,
        plan_validation_workloads,
        resolve_repository_revisions,
        traverse_repository_impact,
    )
    from lou.repository.symbols import extract_changed_symbols
    from lou.telemetry.correlation import correlation_summary

    def event(name: str, body: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(body)}\n\n"

    started = time.monotonic()
    done: list[str] = []

    def finished(stage: str, detail: str) -> str:
        done.append(stage)
        return event(
            "stage",
            {
                "id": stage,
                "status": "done",
                "detail": detail,
                "index": len(done),
                "total": len(STAGES),
                "ms": round((time.monotonic() - started) * 1000),
            },
        )

    try:
        yield event("stages", {"stages": [{"id": i, "label": lbl} for i, lbl in STAGES]})

        revisions = resolve_repository_revisions(
            repository_path=payload.repository_path,
            base_revision=payload.base,
            candidate_revision=payload.candidate,
        )
        run_id = str(uuid.uuid4())
        yield finished(
            "revisions", f"{revisions.base_commit_sha[:8]} → {revisions.candidate_commit_sha[:8]}"
        )

        request = AnalysisRequest(
            repository_id=revisions.repository_root.name,
            repository_path=revisions.repository_root,
            base_commit_sha=revisions.base_commit_sha,
            candidate_commit_sha=revisions.candidate_commit_sha,
            configuration={},
        )
        change = parse_repository_changes(
            repository_id=request.repository_id,
            repository_path=request.repository_path,
            base_revision=request.base_commit_sha,
            candidate_revision=request.candidate_commit_sha,
        )
        touched = len(change.added_files) + len(change.modified_files) + len(change.deleted_files)
        yield finished("diff", f"{touched} file{'s' if touched != 1 else ''} changed")

        change = extract_changed_symbols(repository_path=request.repository_path, change=change)
        yield finished("symbols", f"{len(change.changed_symbols)} changed symbols")

        snapshot = build_repository_graph(
            repository_path=request.repository_path,
            change=change,
            analysis_run_id=run_id,
            artifact_root=ARTIFACTS,
        )
        yield finished("graph", f"{snapshot.completeness:.1%} complete")

        traversal = traverse_repository_impact(snapshot)
        context = build_repository_context(
            traversal,
            repository_id=change.repository_id,
            commit_sha=change.candidate_commit_sha,
        )
        knowledge = _knowledge_graph(snapshot, traversal)
        yield finished(
            "traversal",
            f"{len(context.affected_symbols)} symbols, {len(context.affected_tests)} tests",
        )
        yield event("graph", knowledge)

        registry = load_workload_registry(request.repository_path)
        yield finished("registry", f"{registry.revision} — {len(registry.entries)} declared")

        fallbacks = (
            tuple(e.workload_id for e in registry.entries if e.fallback_eligible)
            if snapshot.completeness < 1
            else ()
        )
        plan = plan_validation_workloads(context, registry, fallback_workload_ids=fallbacks)
        yield finished("plan", f"{len(plan.selected)} selected, {len(plan.omitted)} omitted")

        prediction = predict_impact(
            analysis_run_id=run_id,
            change=change,
            snapshot=snapshot,
            traversal=traversal,
            workloads=plan.selected,
            repository_root=request.repository_path,
        )
        yield finished("prediction", f"{len(prediction.items)} predicted items")

        correlations = {
            name: correlation_summary(name, snapshot)
            for name in sorted({*context.changed_symbols, *context.affected_data_dependencies})
        }
        resolved = sum(1 for c in correlations.values() if c.get("status") == "resolved")
        yield finished("correlation", f"{resolved} of {len(correlations)} resolved")

        yield event(
            "result",
            _result(revisions, run_id, change, context, registry, plan, prediction, correlations)
            | {"knowledge_graph": knowledge},
        )
    except Exception as error:
        yield event("error", {"error": str(error) or error.__class__.__name__})


def _result(
    revisions: Any,
    run_id: str,
    change: Any,
    context: Any,
    registry: Any,
    plan: Any,
    prediction: Any,
    correlations: dict[str, Any],
) -> dict[str, Any]:
    payload = plan.payload()
    return {
        "run_id": run_id,
        "repository": {
            "name": revisions.repository_root.name,
            "path": str(revisions.repository_root),
            "base": revisions.base_commit_sha,
            "candidate": revisions.candidate_commit_sha,
        },
        "change": {
            "added_files": change.added_files,
            "modified_files": change.modified_files,
            "deleted_files": change.deleted_files,
            "changed_symbols": change.changed_symbols,
        },
        "graph": {
            "completeness": context.completeness,
            "changed_symbols": context.changed_symbols,
            "affected_symbols": context.affected_symbols,
            "affected_tests": context.affected_tests,
            "affected_endpoints": context.affected_endpoints,
            "affected_data_dependencies": context.affected_data_dependencies,
            "unresolved_relationships": context.unresolved_relationships,
        },
        "registry_revision": registry.revision,
        "plan": {
            "state": payload["state"],
            "confidence": payload["confidence"],
            "selected": payload["selected"],
            "omitted": payload["omitted"],
            "budget": payload["budget"],
        },
        "prediction": prediction.model_dump(mode="json"),
        "runtime_correlations": correlations,
        "not_measured": [
            "Baseline and candidate workloads were not executed: that needs the Docker sandbox.",
            "No autonomy decision was made, because a decision without measurement would be a "
            "guess rather than evidence.",
        ],
    }


def create_demo_app() -> FastAPI:
    app = FastAPI(title="Lou Demo", version=APP_VERSION, docs_url=None, redoc_url=None)

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(PAGE)

    @app.get("/api/demo")
    def demo() -> JSONResponse:
        return JSONResponse(_demo())

    @app.get("/api/runs")
    def runs(repository_path: str | None = None) -> JSONResponse:
        try:
            return JSONResponse({"runs": _runs(repository_path), "filtered": bool(repository_path)})
        except Exception as error:
            # The page degrades to a message: the database is optional for the
            # analyze and scenario tabs, so a missing one must not break them.
            return JSONResponse(
                {"runs": [], "error": str(error) or error.__class__.__name__}, status_code=200
            )

    @app.get("/api/analyze")
    def analyze(repository_path: str, base: str, candidate: str) -> StreamingResponse:
        payload = AnalyzeRequest(repository_path=repository_path, base=base, candidate=candidate)
        return StreamingResponse(
            _stream(payload),
            media_type="text/event-stream",
            # Without this a proxy may buffer the whole run and defeat the point.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_demo_app()


if __name__ == "__main__":
    import uvicorn

    _demo()
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
