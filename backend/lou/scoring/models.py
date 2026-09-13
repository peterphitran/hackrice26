"""Small scoring DTOs extending the shared version-one contract base."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from contracts.models import ContractModel

UnitValue = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]


class ScoringModel(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DebtInputs(ScoringModel):
    """Already normalized observations; None means unknown, never measured zero."""

    complexity: UnitValue | None = None
    coverage_deficit: UnitValue | None = None
    estimated_patch_size: UnitValue | None = None
    churn: UnitValue | None = None
    graph_centrality: UnitValue | None = None
    runtime_impact: UnitValue | None = None
    path_criticality: UnitValue | None = None
    evidence_confidence: UnitValue | None = None


class RemediationInputs(ScoringModel):
    """Risk of a proposed change, independent of the debt it addresses."""

    blast_radius: UnitValue | None = None
    criticality: UnitValue | None = None
    coverage: UnitValue | None = None
    reversibility: UnitValue | None = None
    verification_strength: UnitValue | None = None
    patch_size: UnitValue | None = None
    schema_migration_risk: UnitValue | None = None
    data_migration_risk: UnitValue | None = None
    context_completeness: UnitValue | None = None
    evidence_confidence: UnitValue | None = None


class FeatureContribution(ScoringModel):
    feature: str
    observed_value: UnitValue | None
    normalized_value: UnitValue
    weight: UnitValue
    contribution: UnitValue
    missing: bool
    explanation: str


class ScoreComponent(ScoringModel):
    value: UnitValue
    contributions: tuple[FeatureContribution, ...]


class DebtScore(ScoringModel):
    rule_revision: Literal["debt-v1"] = "debt-v1"
    principal: UnitValue
    interest: UnitValue
    debt_risk: UnitValue
    confidence: UnitValue
    input_completeness: UnitValue
    missing_inputs: tuple[str, ...]
    inputs: DebtInputs
    components: dict[str, ScoreComponent]
    rationale: tuple[str, ...]


class RemediationScore(ScoringModel):
    rule_revision: Literal["remediation-v1"] = "remediation-v1"
    remediation_risk: UnitValue
    confidence: UnitValue
    input_completeness: UnitValue
    missing_inputs: tuple[str, ...]
    inputs: RemediationInputs
    components: dict[str, ScoreComponent]
    adjustments: tuple[str, ...]
    rationale: tuple[str, ...]
