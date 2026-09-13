"""Set-based prediction evaluation with deterministic calibration summaries."""

from __future__ import annotations

from typing import Literal

from contracts import ImpactEvaluation, ImpactPrediction, ObservedImpact

EXECUTION_OBSERVABLE_KINDS = frozenset({"workload", "runtime_path"})


def evaluate_impact(
    prediction: ImpactPrediction,
    observed: ObservedImpact,
    *,
    graded_kinds: frozenset[str] | None = None,
) -> ImpactEvaluation:
    """Score a prediction against an observation.

    `graded_kinds` restricts scoring to the kinds the observation source can actually
    measure. A predicted kind nothing observes is left ungraded rather than counted as
    a false positive, so precision and recall stay falsifiable.
    """

    def in_scope(kind: str) -> bool:
        return graded_kinds is None or kind in graded_kinds

    predicted = {(item.kind, item.key): item for item in prediction.items if in_scope(item.kind)}
    actual = {(item.kind, item.key): item for item in observed.items if in_scope(item.kind)}
    labels: dict[str, Literal["true_positive", "false_positive", "false_negative"]] = {
        f"{kind}:{key}": "true_positive" if pair in actual else "false_positive"
        for pair in predicted
        for kind, key in [pair]
    }
    labels.update(
        {
            f"{kind}:{key}": "false_negative"
            for kind, key in sorted(actual.keys() - predicted.keys())
        }
    )
    tp = len(predicted.keys() & actual.keys())
    precision = tp / len(predicted) if predicted else 1.0
    recall = tp / len(actual) if actual else 1.0
    mean_score = sum(item.score for item in predicted.values()) / len(predicted) if predicted else 0
    return ImpactEvaluation(
        analysis_run_id=prediction.analysis_run_id,
        repository_id=prediction.repository_id,
        predictor_revision=prediction.predictor_revision,
        labels=labels,
        precision=precision,
        recall=recall,
        false_negative_rate=1 - recall,
        calibration={
            "mean_predicted_score": mean_score,
            "observed_hit_rate": precision,
            "calibration_error": abs(mean_score - precision),
            "graded_items": float(len(predicted)),
            "ungraded_items": float(len(prediction.items) - len(predicted)),
            "observed_items": float(len(actual)),
        },
    )
