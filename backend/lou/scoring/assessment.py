"""Version-two assessments built from observed, source-labelled inputs."""

from contracts import (
    ConfidenceBounds,
    CostEstimates,
    DebtAssessment,
    ExpectedValue,
    RemediationRisk,
    RiskFeature,
    RiskSignals,
)
from lou.scoring.models import DebtInputs, RemediationInputs
from lou.scoring.rules import score_debt, score_remediation


def _feature(
    name: str, value: float | None, contribution: float | None, signals: RiskSignals
) -> RiskFeature:
    return RiskFeature(
        name=name,
        value=value,
        contribution=contribution,
        source=signals.sources.get(name),
        evidence_id=signals.evidence_ids.get(name),
        missing_reason="not_observed" if value is None else None,
    )


def assess_debt(
    run_id: str, inputs: DebtInputs, signals: RiskSignals | None = None
) -> DebtAssessment:
    """Retain v1 principal; add incident and ownership evidence to interest."""
    extra = signals or RiskSignals()
    original = score_debt(inputs)
    weights = (
        ("churn", inputs.churn, 0.20),
        ("graph_centrality", inputs.graph_centrality, 0.18),
        ("runtime_impact", inputs.runtime_impact, 0.20),
        ("path_criticality", inputs.path_criticality, 0.18),
        ("incident_burden", extra.incident_burden, 0.14),
        ("ownership_gap", extra.ownership_gap, 0.10),
    )
    interest_factor = sum((value or 0.0) * weight for _, value, weight in weights)
    missing_interest = sum(weight for _, value, weight in weights if value is None)
    principal_missing = sum(
        weight
        for name, weight in (
            ("complexity", 0.35),
            ("coverage_deficit", 0.35),
            ("estimated_patch_size", 0.30),
        )
        if getattr(inputs, name) is None
    )
    principal = original.principal
    interest = principal * interest_factor
    risk = (principal + interest) / 2
    upper_principal = min(1.0, principal + principal_missing)
    upper_risk = (
        upper_principal + upper_principal * min(1.0, interest_factor + missing_interest)
    ) / 2
    observed = sum(value is not None for _, value, _ in weights) + sum(
        getattr(inputs, name) is not None
        for name in ("complexity", "coverage_deficit", "estimated_patch_size")
    )
    confidence = observed / 9 * (inputs.evidence_confidence or 0.0)
    principal_features = tuple(
        _feature(name, getattr(inputs, name), (getattr(inputs, name) or 0.0) * weight / 2, extra)
        for name, weight in (
            ("complexity", 0.35),
            ("coverage_deficit", 0.35),
            ("estimated_patch_size", 0.30),
        )
    )
    interest_features = tuple(
        _feature(name, value, (value or 0.0) * weight * principal / 2, extra)
        for name, value, weight in weights
    )
    features = (*principal_features, *interest_features)
    return DebtAssessment(
        analysis_run_id=run_id,
        rule_revision="debt-v2",
        principal=principal,
        interest=interest,
        debt_risk=risk,
        confidence=confidence,
        bounds=ConfidenceBounds(lower=risk, upper=upper_risk),
        features=features,
        missing_inputs=tuple(item.name for item in features if item.value is None),
    )


def assess_remediation(
    run_id: str, inputs: RemediationInputs, signals: RiskSignals | None = None
) -> RemediationRisk:
    extra = signals or RiskSignals()
    score = score_remediation(inputs)
    terms = score.components["remediation_risk"].contributions
    features = tuple(
        _feature(item.feature, item.observed_value, item.contribution, extra) for item in terms
    )
    flags: list[str] = []
    if inputs.schema_migration_risk is None or inputs.schema_migration_risk > 0:
        flags.append("schema_migration")
    if inputs.data_migration_risk is None or inputs.data_migration_risk > 0:
        flags.append("data_migration")
    if inputs.coverage is None or inputs.coverage < 0.8:
        flags.append("low_or_unknown_coverage")
    if extra.security_sensitive is None or extra.security_sensitive:
        flags.append("security_sensitive_or_unchecked")
    lower = max(
        0.0, score.remediation_risk - sum(item.contribution for item in terms if item.missing)
    )
    confidence = score.confidence * (1.0 if extra.security_sensitive is not None else 0.5)
    assessment = RemediationRisk(
        analysis_run_id=run_id,
        rule_revision="remediation-v2",
        risk=score.remediation_risk,
        confidence=confidence,
        bounds=ConfidenceBounds(lower=lower, upper=score.remediation_risk),
        features=features,
        missing_inputs=(
            *score.missing_inputs,
            *(("security_sensitive",) if extra.security_sensitive is None else ()),
        ),
        hard_risk_flags=tuple(flags),
    )
    return apply_hard_risk_flags(assessment, ())


def apply_hard_risk_flags(
    assessment: RemediationRisk, extra_flags: tuple[str, ...]
) -> RemediationRisk:
    """Make a hard safety flag visible in both the score and autonomy policy."""
    flags = tuple(sorted(set(assessment.hard_risk_flags) | set(extra_flags)))
    if not flags:
        return assessment
    floor = 0.5
    uplift = max(0.0, floor - assessment.risk)
    features = assessment.features
    if uplift:
        features = (
            *features,
            RiskFeature(
                name="hard_risk_floor",
                value=1.0,
                contribution=uplift,
                source="remediation-v2-hard-risk-rule",
            ),
        )
    return RemediationRisk.model_validate(
        {
            **assessment.model_dump(mode="json"),
            "risk": max(assessment.risk, floor),
            "bounds": {
                "lower": max(assessment.bounds.lower, floor),
                "upper": max(assessment.bounds.upper, floor),
            },
            "features": features,
            "hard_risk_flags": flags,
        }
    )


def estimate_value(inputs: CostEstimates | None) -> ExpectedValue:
    """Use independent hour estimates; normalized debt points are never converted."""
    if inputs is None:
        return ExpectedValue(
            status="insufficient_evidence", horizon_days=30, missing_inputs=("cost_estimates",)
        )
    names = (
        "debt_avoided_hours",
        "remediation_hours",
        "verification_hours",
        "regression_probability",
        "regression_loss_hours",
    )
    missing = tuple(
        name for name in names if getattr(inputs, name) is None or not inputs.sources.get(name)
    )
    if missing:
        return ExpectedValue(
            status="insufficient_evidence", horizon_days=inputs.horizon_days, missing_inputs=missing
        )
    debt = inputs.debt_avoided_hours
    repair = inputs.remediation_hours
    verify = inputs.verification_hours
    probability = inputs.regression_probability
    loss = inputs.regression_loss_hours
    assert debt is not None and repair is not None and verify is not None
    assert probability is not None and loss is not None
    central = debt - repair - verify - probability * loss
    uncertainty = inputs.uncertainty_fraction
    lower = debt * (1 - uncertainty) - (repair + verify + probability * loss) * (1 + uncertainty)
    upper = debt * (1 + uncertainty) - (repair + verify + probability * loss) * (1 - uncertainty)
    return ExpectedValue(
        status="estimated",
        horizon_days=inputs.horizon_days,
        central_hours=central,
        lower_hours=lower,
        upper_hours=upper,
        components_hours={
            "debt_avoided": debt,
            "remediation_effort": repair,
            "verification_cost": verify,
            "expected_regression_cost": probability * loss,
        },
        sources=inputs.sources,
    )
