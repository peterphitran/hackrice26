from collections.abc import Mapping
from math import fsum
from typing import Any, Literal, TypedDict, cast

import pytest
from pydantic import ValidationError

from lou.scoring import (
    DebtInputs,
    DebtScore,
    RemediationInputs,
    RemediationScore,
    score_debt,
    score_remediation,
)

DEBT_FEATURES = (
    "complexity",
    "coverage_deficit",
    "estimated_patch_size",
    "churn",
    "graph_centrality",
    "runtime_impact",
    "path_criticality",
)
REMEDIATION_FEATURES = (
    "blast_radius",
    "criticality",
    "coverage",
    "reversibility",
    "verification_strength",
    "patch_size",
    "schema_migration_risk",
    "data_migration_risk",
)
SAFE_PATCH = {
    "blast_radius": 0.0,
    "criticality": 0.0,
    "coverage": 1.0,
    "reversibility": 1.0,
    "verification_strength": 1.0,
    "patch_size": 0.0,
    "schema_migration_risk": 0.0,
    "data_migration_risk": 0.0,
    "context_completeness": 1.0,
    "evidence_confidence": 1.0,
}


class _DebtValues(TypedDict, total=False):
    schema_version: Literal["1"]
    complexity: float | None
    coverage_deficit: float | None
    estimated_patch_size: float | None
    churn: float | None
    graph_centrality: float | None
    runtime_impact: float | None
    path_criticality: float | None
    evidence_confidence: float | None


class _RemediationValues(TypedDict, total=False):
    schema_version: Literal["1"]
    blast_radius: float | None
    criticality: float | None
    coverage: float | None
    reversibility: float | None
    verification_strength: float | None
    patch_size: float | None
    schema_migration_risk: float | None
    data_migration_risk: float | None
    context_completeness: float | None
    evidence_confidence: float | None


def _debt(values: Mapping[str, float | None]) -> DebtInputs:
    return DebtInputs(**cast(_DebtValues, values))


def _remediation(values: Mapping[str, float | None]) -> RemediationInputs:
    return RemediationInputs(**cast(_RemediationValues, values))


@pytest.mark.parametrize(
    "value,principal,interest,risk",
    [
        (0.0, 0.0, 0.0, 0.0),
        (0.5, 0.5, 0.25, 0.375),
        (1.0, 1.0, 1.0, 1.0),
    ],
)
def test_debt_documented_formula(value: Any, principal: Any, interest: Any, risk: Any) -> None:
    inputs = _debt(dict.fromkeys(DEBT_FEATURES, value) | {"evidence_confidence": 1.0})
    score = score_debt(inputs)
    assert score.principal == pytest.approx(principal)
    assert score.interest == pytest.approx(interest)
    assert score.debt_risk == pytest.approx(risk)
    assert score.confidence == 1.0
    assert score.missing_inputs == ()


def test_debt_unequal_features_and_explainable_contributions() -> None:
    score = score_debt(
        DebtInputs(
            complexity=0.8,
            coverage_deficit=0.4,
            estimated_patch_size=0.2,
            churn=0.6,
            graph_centrality=0.7,
            runtime_impact=0.9,
            path_criticality=1.0,
            evidence_confidence=0.9,
        )
    )
    assert score.principal == pytest.approx(0.48)
    assert score.components["interest_rate"].value == pytest.approx(0.78)
    assert score.interest == pytest.approx(0.3744)
    assert score.debt_risk == pytest.approx(0.4272)
    assert score.confidence == 0.9
    assert score.rule_revision == "debt-v1"
    for component in score.components.values():
        assert fsum(t.contribution for t in component.contributions) == pytest.approx(
            component.value
        )
        for term in component.contributions:
            assert 0 <= term.normalized_value <= 1
            assert term.contribution == pytest.approx(term.normalized_value * term.weight)
            assert term.explanation


@pytest.mark.parametrize("missing", DEBT_FEATURES)
def test_missing_debt_feature_lowers_confidence_and_never_increases_priority(missing: Any) -> None:
    known = dict.fromkeys(DEBT_FEATURES, 0.5) | {"evidence_confidence": 0.9}
    complete = score_debt(_debt(known))
    score = score_debt(_debt(known | {missing: None}))
    assert score.confidence == pytest.approx(0.9 * 6 / 7)
    assert score.confidence < complete.confidence
    assert score.debt_risk <= complete.debt_risk
    assert score.missing_inputs == (missing,)
    terms = [t for c in score.components.values() for t in c.contributions if t.feature == missing]
    assert terms
    assert all(t.missing and t.observed_value is None and t.normalized_value == 0 for t in terms)


def test_all_missing_is_distinct_from_measured_zero() -> None:
    debt = score_debt(DebtInputs())
    remediation = score_remediation(RemediationInputs())
    assert debt.principal == debt.interest == debt.debt_risk == debt.confidence == 0
    assert len(debt.missing_inputs) == 8
    assert remediation.remediation_risk == 1
    assert remediation.confidence == 0
    assert len(remediation.missing_inputs) == 10
    assert score_remediation(_remediation(SAFE_PATCH)).confidence == 1


@pytest.mark.parametrize("risk_value", [0.0, 0.5, 1.0])
def test_remediation_normal_and_boundary_cases(risk_value: Any) -> None:
    values = dict.fromkeys(REMEDIATION_FEATURES, risk_value)
    for name in ("coverage", "reversibility", "verification_strength"):
        values[name] = 1 - risk_value
    score = score_remediation(
        RemediationInputs(
            **(values | {"context_completeness": 1.0, "evidence_confidence": 1.0}),
        )
    )
    assert score.remediation_risk == pytest.approx(risk_value)
    assert score.confidence == 1
    assert score.rule_revision == "remediation-v1"
    component = score.components["remediation_risk"]
    assert fsum(t.contribution for t in component.contributions) == pytest.approx(risk_value)


@pytest.mark.parametrize(
    "feature,weight",
    [
        ("blast_radius", 0.20),
        ("criticality", 0.15),
        ("coverage", 0.15),
        ("reversibility", 0.15),
        ("verification_strength", 0.15),
        ("patch_size", 0.10),
    ],
)
def test_each_patch_feature_has_expected_direction_and_weight(feature: Any, weight: Any) -> None:
    values = SAFE_PATCH | {feature: 1 - SAFE_PATCH[feature]}
    score = score_remediation(_remediation(values))
    assert score.remediation_risk == pytest.approx(weight)


@pytest.mark.parametrize("feature", ["schema_migration_risk", "data_migration_risk"])
@pytest.mark.parametrize("value,expected", [(0.0, 0.0), (0.2, 0.085), (1.0, 0.125), (None, 0.125)])
def test_migrations_cannot_be_trivially_reversible(feature: Any, value: Any, expected: Any) -> None:
    score = score_remediation(_remediation(SAFE_PATCH | {feature: value}))
    assert score.remediation_risk == pytest.approx(expected)
    term = next(
        t
        for t in score.components["remediation_risk"].contributions
        if t.feature == "reversibility"
    )
    assert term.observed_value == 1
    assert term.normalized_value == (0.0 if value == 0 else 0.5)
    assert bool(score.adjustments) is (value != 0)


@pytest.mark.parametrize("feature", REMEDIATION_FEATURES)
def test_missing_remediation_feature_is_explicit_and_conservative(feature: Any) -> None:
    score = score_remediation(_remediation(SAFE_PATCH | {feature: None}))
    assert score.missing_inputs == (feature,)
    assert score.confidence == 7 / 8
    assert score.remediation_risk > 0
    term = next(
        t for t in score.components["remediation_risk"].contributions if t.feature == feature
    )
    assert term.missing and term.observed_value is None and term.normalized_value == 1


@pytest.mark.parametrize(
    "context,evidence,expected",
    [
        (1.0, 1.0, 1.0),
        (0.5, 0.8, 0.4),
        (0.0, 1.0, 0.0),
        (None, 1.0, 0.0),
        (1.0, None, 0.0),
    ],
)
def test_context_and_evidence_confidence_penalties(
    context: Any, evidence: Any, expected: Any
) -> None:
    score = score_remediation(
        _remediation(
            SAFE_PATCH | {"context_completeness": context, "evidence_confidence": evidence}
        )
    )
    assert score.confidence == pytest.approx(expected)
    assert ("context_completeness" in score.missing_inputs) is (context is None)
    assert ("evidence_confidence" in score.missing_inputs) is (evidence is None)


@pytest.mark.parametrize(
    "model,fields",
    [
        (DebtInputs, (*DEBT_FEATURES, "evidence_confidence")),
        (RemediationInputs, (*REMEDIATION_FEATURES, "context_completeness", "evidence_confidence")),
    ],
)
@pytest.mark.parametrize(
    "invalid", [-0.001, 1.001, float("nan"), float("inf"), -float("inf"), True, "0.5"]
)
def test_invalid_normalized_values_are_rejected(model: Any, fields: Any, invalid: Any) -> None:
    for feature in fields:
        with pytest.raises(ValidationError):
            model(**{feature: invalid})


@pytest.mark.parametrize("model", [DebtInputs, RemediationInputs])
def test_input_contract_rejects_unknown_fields_and_versions(model: Any) -> None:
    with pytest.raises(ValidationError):
        model(schema_version="2")
    with pytest.raises(ValidationError):
        model(unknown=0.5)


def test_scores_are_deterministic_and_json_round_trip() -> None:
    debt = _debt(dict.fromkeys(DEBT_FEATURES, 0.5) | {"evidence_confidence": 0.9})
    patch = _remediation(SAFE_PATCH)
    cases: list[tuple[Any, Any, Any]] = [
        (score_debt, debt, DebtScore),
        (score_remediation, patch, RemediationScore),
    ]
    for scorer, inputs, output_model in cases:
        score = scorer(inputs)
        assert scorer(inputs).model_dump_json() == score.model_dump_json()
        assert output_model.model_validate_json(score.model_dump_json()) == score


def test_debt_does_not_change_when_patch_risk_changes() -> None:
    inputs = _debt(dict.fromkeys(DEBT_FEATURES, 0.5) | {"evidence_confidence": 1.0})
    before = score_debt(inputs)
    safe = score_remediation(_remediation(SAFE_PATCH))
    risky = score_remediation(_remediation(SAFE_PATCH | {"blast_radius": 1.0}))
    assert safe.remediation_risk < risky.remediation_risk
    assert score_debt(inputs) == before
