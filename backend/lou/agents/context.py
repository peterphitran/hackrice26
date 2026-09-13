"""Versioned, bounded evidence supplied to a diagnosis or patch provider."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts import (
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)


class BundleBudget(BaseModel):
    """Limits for included items; omissions remain visible outside these limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=8, ge=0)
    max_bytes: int = Field(default=16_384, ge=0)
    max_tokens: int = Field(default=4_096, ge=0)
    max_unresolved_relationships: int = Field(default=20, ge=0)


class RepositoryText(BaseModel):
    """Optional repository bytes supplied by a caller, never provider instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["diff", "file_content", "commit_message"]
    text: str
    path: str | None = None


class BundleItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    inclusion_reason: str
    trust: Literal["recorded_fixture", "untrusted_repository"]
    file_paths: tuple[str, ...] = ()
    payload: dict[str, Any]


class OmittedContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    reason: str
    source: str


class AgentContextBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    analysis_run_id: str
    base_commit_sha: str
    candidate_commit_sha: str
    items: tuple[BundleItem, ...]
    omitted: tuple[OmittedContext, ...]
    unresolved_relationships: tuple[str, ...]
    budget: BundleBudget
    used_file_count: int
    used_byte_count: int
    used_token_count: int
    token_estimator: Literal["utf8_bytes_divided_by_4_rounded_up"] = (
        "utf8_bytes_divided_by_4_rounded_up"
    )

    def provider_data_json(self) -> str:
        """Serialize data as JSON; repository text stays in a marked, escaped value."""
        return self.model_dump_json()


def _item_cost(item: BundleItem) -> tuple[int, int]:
    byte_count = len(item.model_dump_json().encode("utf-8"))
    return byte_count, (byte_count + 3) // 4


def build_context_bundle(
    job: AnalysisJob,
    context: RepositoryContext,
    finding: Finding,
    candidate: VerificationResult,
    workloads: Sequence[WorkloadSelection],
    patch: PatchArtifact,
    *,
    budget: BundleBudget | None = None,
    repository_texts: Sequence[RepositoryText] = (),
) -> AgentContextBundle:
    """Build a deterministic bundle from frozen records and explicitly label gaps."""
    if not (
        job.analysis_run_id
        == finding.analysis_run_id
        == candidate.analysis_run_id
        == patch.analysis_run_id
    ):
        raise ValueError("Analysis run IDs must match across context records")
    if context.repository_id != job.repository_id:
        raise ValueError("Repository IDs must match")
    if not (context.commit_sha == candidate.commit_sha == job.candidate_commit_sha):
        raise ValueError("Candidate commit SHAs must match")
    if patch.base_commit_sha != job.candidate_commit_sha:
        raise ValueError("Expected patch must be based on the candidate commit")
    if finding.phase != "candidate" or candidate.phase != "candidate":
        raise ValueError("Finding and verification must be candidate phase")

    limit = budget or BundleBudget()
    included: list[BundleItem] = []
    omitted: list[OmittedContext] = []
    file_paths: set[str] = set()
    used_bytes = 0
    used_tokens = 0

    def add(item: BundleItem, source: str) -> None:
        nonlocal used_bytes, used_tokens
        item_bytes, item_tokens = _item_cost(item)
        next_files = file_paths | set(item.file_paths)
        exceeded = [
            name
            for name, over in (
                ("file-count", len(next_files) > limit.max_files),
                ("byte-size", used_bytes + item_bytes > limit.max_bytes),
                ("token-count", used_tokens + item_tokens > limit.max_tokens),
            )
            if over
        ]
        if exceeded:
            omitted.append(
                OmittedContext(
                    key=item.key,
                    reason="Exceeded " + ", ".join(exceeded) + " budget.",
                    source=source,
                )
            )
            return
        included.append(item)
        file_paths.update(item.file_paths)
        used_bytes += item_bytes
        used_tokens += item_tokens

    add(
        BundleItem(
            key="candidate_change",
            inclusion_reason="Identifies the changed checkout symbol and expected repair.",
            trust="recorded_fixture",
            file_paths=(finding.file_path,) if finding.file_path else (),
            payload={
                "base_commit_sha": job.base_commit_sha,
                "candidate_commit_sha": job.candidate_commit_sha,
                "changed_symbols": context.changed_symbols,
                "file_path": finding.file_path,
                "expected_change": patch.metadata.get("expected_change"),
            },
        ),
        "analysis_job.json + repository_context.json + patch_artifact.json",
    )
    omitted.append(
        OmittedContext(
            key="candidate_diff",
            reason="No candidate unified diff is recorded in the checkout fixtures.",
            source="demo_checkout/patch_artifact.json",
        )
    )
    add(
        BundleItem(
            key="candidate_finding",
            inclusion_reason="Explains the measured N+1 regression and its source location.",
            trust="recorded_fixture",
            file_paths=(finding.file_path,) if finding.file_path else (),
            payload=finding.model_dump(mode="json"),
        ),
        "finding.json",
    )
    add(
        BundleItem(
            key="candidate_verification",
            inclusion_reason="Shows failed candidate measurements and passing unit tests.",
            trust="recorded_fixture",
            payload=candidate.model_dump(mode="json"),
        ),
        "verification_candidate.json",
    )
    add(
        BundleItem(
            key="selected_graph_context",
            inclusion_reason="Connects the changed symbol to tests, endpoint, and workloads.",
            trust="recorded_fixture",
            payload=context.model_dump(mode="json"),
        ),
        "repository_context.json",
    )

    selected = set(context.selected_workload_ids)
    for workload in workloads:
        if workload.workload_id not in selected:
            omitted.append(
                OmittedContext(
                    key=f"workload:{workload.workload_id}",
                    reason="The repository graph did not select this workload.",
                    source="repository_context.json",
                )
            )
            continue
        if workload.phase != "candidate":
            raise ValueError("Selected workloads must be candidate phase")
        add(
            BundleItem(
                key=f"workload:{workload.workload_id}",
                inclusion_reason=context.selection_reasons.get(
                    workload.workload_id, workload.reason
                ),
                trust="recorded_fixture",
                file_paths=(workload.definition_path,),
                payload=workload.model_dump(mode="json"),
            ),
            f"workload_{workload.workload_type}.json",
        )
    supplied = {workload.workload_id for workload in workloads}
    for missing_id in sorted(selected - supplied):
        omitted.append(
            OmittedContext(
                key=f"workload:{missing_id}",
                reason="Selected workload definition was not supplied.",
                source="repository_context.json",
            )
        )
    if not context.affected_tests:
        omitted.append(
            OmittedContext(
                key="affected_tests",
                reason="No affected test relationship was resolved.",
                source="repository_context.json",
            )
        )
    if not context.affected_endpoints:
        omitted.append(
            OmittedContext(
                key="affected_endpoints",
                reason="No affected endpoint relationship was resolved.",
                source="repository_context.json",
            )
        )
    for index, record in enumerate(repository_texts):
        add(
            BundleItem(
                key=f"repository_text:{index}",
                inclusion_reason=f"Caller supplied {record.kind} as untrusted evidence.",
                trust="untrusted_repository",
                file_paths=(record.path,) if record.path else (),
                payload={
                    "kind": record.kind,
                    "path": record.path,
                    "untrusted_repository_data": record.text,
                },
            ),
            "caller-supplied repository text",
        )

    unresolved = tuple(context.unresolved_relationships[: limit.max_unresolved_relationships])
    if len(unresolved) < len(context.unresolved_relationships):
        omitted.append(
            OmittedContext(
                key="unresolved_relationships",
                reason=(
                    f"{len(context.unresolved_relationships) - len(unresolved)} "
                    "unresolved relationships exceed the recorded limit."
                ),
                source="repository_context.json",
            )
        )
    return AgentContextBundle(
        analysis_run_id=job.analysis_run_id,
        base_commit_sha=job.base_commit_sha,
        candidate_commit_sha=job.candidate_commit_sha,
        items=tuple(included),
        omitted=tuple(omitted),
        unresolved_relationships=unresolved,
        budget=limit,
        used_file_count=len(file_paths),
        used_byte_count=used_bytes,
        used_token_count=used_tokens,
    )
