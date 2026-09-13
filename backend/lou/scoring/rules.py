"""Pure, deterministic debt and proposed-remediation heuristics."""

from fractions import Fraction

from lou.scoring.models import (
    DebtInputs,
    DebtScore,
    FeatureContribution,
    RemediationInputs,
    RemediationScore,
    ScoreComponent,
)

PRINCIPAL_WEIGHTS = (
    ("complexity", 0.35),
    ("coverage_deficit", 0.35),
    ("estimated_patch_size", 0.30),
)
INTEREST_WEIGHTS = (
    ("churn", 0.30),
    ("graph_centrality", 0.25),
    ("runtime_impact", 0.25),
    ("path_criticality", 0.20),
)
REMEDIATION_WEIGHTS = (
    ("blast_radius", 0.20),
    ("criticality", 0.15),
    ("coverage", 0.15),
    ("reversibility", 0.15),
    ("verification_strength", 0.15),
    ("patch_size", 0.10),
    ("schema_migration_risk", 0.05),
    ("data_migration_risk", 0.05),
)
PROTECTIVE_FEATURES = frozenset({"coverage", "reversibility", "verification_strength"})


def _component(contributions: list[FeatureContribution]) -> ScoreComponent:
    return ScoreComponent(
        value=min(1.0, float(sum(Fraction(str(item.contribution)) for item in contributions))),
        contributions=tuple(contributions),
    )


def _term(
    name: str, observed: float | None, normalized: float, weight: float, explanation: str
) -> FeatureContribution:
    return FeatureContribution(
        feature=name,
        observed_value=observed,
        normalized_value=normalized,
        weight=weight,
        contribution=float(Fraction(str(normalized)) * Fraction(str(weight))),
        missing=observed is None,
        explanation=explanation,
    )


def score_debt(inputs: DebtInputs) -> DebtScore:
    """Unknown debt observations contribute zero, producing a lower-bound estimate."""
    values = inputs.model_dump(exclude={"schema_version"})
    missing = tuple(name for name, value in values.items() if value is None)
    principal = _component(
        [
            _term(name, values[name], values[name] or 0.0, weight, "Weighted debt principal.")
            for name, weight in PRINCIPAL_WEIGHTS
        ]
    )
    interest_rate = _component(
        [
            _term(name, values[name], values[name] or 0.0, weight, "Weighted debt growth factor.")
            for name, weight in INTEREST_WEIGHTS
        ]
    )
    interest = _component(
        [
            _term(
                term.feature,
                term.observed_value,
                term.normalized_value,
                float(Fraction(str(term.weight)) * Fraction(str(principal.value))),
                "Growth contribution multiplied by principal.",
            )
            for term in interest_rate.contributions
        ]
    )
    risk = _component(
        [
            _term("principal", principal.value, principal.value, 0.5, "Half of principal."),
            _term("interest", interest.value, interest.value, 0.5, "Half of interest."),
        ]
    )
    completeness = (
        sum(values[name] is not None for name, _ in (*PRINCIPAL_WEIGHTS, *INTEREST_WEIGHTS)) / 7
    )
    return DebtScore(
        principal=principal.value,
        interest=interest.value,
        debt_risk=risk.value,
        confidence=completeness * (inputs.evidence_confidence or 0.0),
        input_completeness=completeness,
        missing_inputs=missing,
        inputs=inputs,
        components={
            "principal": principal,
            "interest_rate": interest_rate,
            "interest": interest,
            "debt_risk": risk,
        },
        rationale=(
            "Principal estimates today's repair effort in normalized debt points.",
            "Interest multiplies principal by churn, centrality, runtime impact, and criticality.",
            "Debt risk is (principal + interest) / 2; it does not measure patch safety.",
            "Unknown features contribute zero and reduce confidence; zero is not proof of no debt."
            if missing
            else "All debt inputs are present.",
        ),
    )


def score_remediation(inputs: RemediationInputs) -> RemediationScore:
    """Unknown safety observations assume worst-case risk and reduce confidence."""
    values = inputs.model_dump(exclude={"schema_version"})
    missing = tuple(name for name, value in values.items() if value is None)
    migrations = (inputs.schema_migration_risk, inputs.data_migration_risk)
    migration_possible = any(value is None or value > 0 for value in migrations)
    adjustments: list[str] = []
    contributions: list[FeatureContribution] = []
    for name, weight in REMEDIATION_WEIGHTS:
        observed = values[name]
        normalized = 1.0 if observed is None else observed
        explanation = "Higher values increase remediation risk."
        if name in PROTECTIVE_FEATURES:
            normalized = 1.0 if observed is None else float(1 - Fraction(str(observed)))
            explanation = "Risk uses one minus the protective feature."
        if name == "reversibility" and migration_possible and normalized < 0.5:
            normalized = 0.5
            adjustments.append("migration_reversibility_floor")
            explanation = "Known or possible migrations impose at least 0.5 irreversibility risk."
        contributions.append(_term(name, observed, normalized, weight, explanation))
    weighted_risk = _component(contributions)
    completeness = sum(values[name] is not None for name, _ in REMEDIATION_WEIGHTS) / 8
    context = inputs.context_completeness or 0.0
    return RemediationScore(
        remediation_risk=weighted_risk.value,
        confidence=completeness * context * (inputs.evidence_confidence or 0.0),
        input_completeness=completeness,
        missing_inputs=missing,
        inputs=inputs,
        components={"remediation_risk": weighted_risk},
        adjustments=tuple(adjustments),
        rationale=(
            "Remediation risk describes changing the code, separately from leaving its debt.",
            "Coverage, reversibility, and verification strength reduce risk when supported.",
            "Unknown features use worst-case risk; "
            "confidence also scales with context completeness.",
            "Schema or data migration cannot be considered trivially reversible."
            if migration_possible
            else "No schema or data migration risk was reported.",
        ),
    )
