from contracts import ImpactItem, ImpactPrediction, ObservedImpact, RepositoryChange
from lou.prediction.baselines import changed_file_baseline
from lou.prediction.evaluate import evaluate_impact


def _prediction(items: tuple[ImpactItem, ...]) -> ImpactPrediction:
    return ImpactPrediction(
        analysis_run_id="run", repository_id="repo", base_commit_sha="a", candidate_commit_sha="b",
        items=items, confidence=0.8,
    )


def test_evaluation_labels_tp_fp_fn_and_metrics() -> None:
    prediction = _prediction((ImpactItem(kind="symbol", key="a", score=1, reason="x"),))
    observed = ObservedImpact(
        analysis_run_id="run",
        items=(
            ImpactItem(kind="symbol", key="a", score=1, reason="x"),
            ImpactItem(kind="workload", key="w", score=1, reason="x"),
        ),
    )
    result = evaluate_impact(prediction, observed)
    assert result.labels == {"symbol:a": "true_positive", "workload:w": "false_negative"}
    assert result.precision == 1 and result.recall == 0.5


def test_changed_file_baseline_is_stable_and_keeps_workloads() -> None:
    change = RepositoryChange(repository_id="repo", base_commit_sha="a", candidate_commit_sha="b", modified_files=["z.py", "a.py"])
    result = changed_file_baseline(analysis_run_id="run", change=change)
    assert [item.key for item in result.items] == ["a.py", "z.py"]
    assert result.predictor_revision == "baseline-v1"
