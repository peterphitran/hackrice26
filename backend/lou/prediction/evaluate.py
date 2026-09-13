"""Set-based prediction evaluation with deterministic calibration summaries."""

from __future__ import annotations

from typing import Literal

from contracts import ImpactEvaluation, ImpactPrediction, ObservedImpact


def evaluate_impact(prediction: ImpactPrediction, observed: ObservedImpact) -> ImpactEvaluation:
    predicted = {(item.kind, item.key): item for item in prediction.items}
    actual = {(item.kind, item.key): item for item in observed.items}
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
    return ImpactEvaluation(
        analysis_run_id=prediction.analysis_run_id,
        repository_id=prediction.repository_id,
        predictor_revision=prediction.predictor_revision,
        labels=labels,
        precision=precision,
        recall=recall,
        false_negative_rate=1 - recall,
        calibration={"mean_predicted_confidence": prediction.confidence},
    )
