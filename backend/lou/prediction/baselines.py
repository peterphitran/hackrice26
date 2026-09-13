"""Reference selectors used to judge whether graph prediction adds value."""

from __future__ import annotations

from contracts import ImpactItem, ImpactPrediction, RepositoryChange, WorkloadSelection


def changed_file_baseline(
    *, analysis_run_id: str, change: RepositoryChange, workloads: tuple[WorkloadSelection, ...] = ()
) -> ImpactPrediction:
    """Predict only changed files and preserve every supplied workload."""

    items = [
        ImpactItem(
            kind="symbol",
            key=path,
            score=1.0,
            reason="changed file baseline",
            provenance=["git-diff"],
        )
        for path in sorted(
            set(change.added_files) | set(change.modified_files) | set(change.deleted_files)
        )
    ]
    items.extend(
        ImpactItem(
            kind="workload",
            key=item.workload_id,
            score=1.0,
            reason="required baseline workload",
            provenance=["workload-registry"],
        )
        for item in workloads
    )
    return ImpactPrediction(
        analysis_run_id=analysis_run_id,
        repository_id=change.repository_id,
        base_commit_sha=change.base_commit_sha,
        candidate_commit_sha=change.candidate_commit_sha,
        items=tuple(sorted(items, key=lambda item: (item.kind, item.key))),
        required_workload_ids=tuple(sorted({item.workload_id for item in workloads})),
        confidence=1.0,
        predictor_name="changed-file-baseline",
        predictor_revision="baseline-v1",
    )
