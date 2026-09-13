"""Version-two, explainable risk and policy records.

Version one remains readable by existing CLI and persistence consumers. These
records are the stable M5 boundary and never reinterpret a v1 score as money.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]
Hours = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]
Level = Annotated[int, Field(ge=0, le=5, strict=True)]


class RiskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["2"] = "2"


class RiskFeature(RiskContract):
    name: str
    value: Unit | None
    contribution: Unit | None
    source: str | None = None
    evidence_id: str | None = None
    missing_reason: str | None = None


class ConfidenceBounds(RiskContract):
    """Scenario bounds, not a calibrated statistical confidence interval."""

    lower: Unit
    upper: Unit
    kind: Literal["scenario", "calibrated"] = "scenario"

    @model_validator(mode="after")
    def ordered(self) -> "ConfidenceBounds":
        if self.lower > self.upper:
            raise ValueError("lower bound exceeds upper bound")
        return self


class DebtAssessment(RiskContract):
    analysis_run_id: str
    rule_revision: str
    principal: Unit
    interest: Unit
    debt_risk: Unit
    confidence: Unit
    bounds: ConfidenceBounds
    features: tuple[RiskFeature, ...]
    missing_inputs: tuple[str, ...]


class RemediationRisk(RiskContract):
    analysis_run_id: str
    rule_revision: str
    risk: Unit
    confidence: Unit
    bounds: ConfidenceBounds
    features: tuple[RiskFeature, ...]
    missing_inputs: tuple[str, ...]
    hard_risk_flags: tuple[str, ...]


class ExpectedValue(RiskContract):
    """All monetary-looking numbers here are engineering hours over one horizon."""

    status: Literal["estimated", "insufficient_evidence"]
    horizon_days: int = Field(ge=1)
    unit: Literal["engineering_hours"] = "engineering_hours"
    central_hours: float | None = Field(default=None, allow_inf_nan=False)
    lower_hours: float | None = Field(default=None, allow_inf_nan=False)
    upper_hours: float | None = Field(default=None, allow_inf_nan=False)
    missing_inputs: tuple[str, ...] = ()
    components_hours: dict[str, float] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    bounds_note: str = "Scenario bounds from stated uncertainty; not statistically calibrated."

    @model_validator(mode="after")
    def consistent(self) -> "ExpectedValue":
        values = (self.lower_hours, self.central_hours, self.upper_hours)
        if self.status == "estimated":
            if any(value is None for value in values):
                raise ValueError("estimated value requires all three scenarios")
            if not self.lower_hours <= self.central_hours <= self.upper_hours:  # type: ignore[operator]
                raise ValueError("expected-value scenarios are out of order")
        elif any(value is not None for value in values):
            raise ValueError("unknown value cannot contain a numeric estimate")
        return self


class AutonomyDecision(RiskContract):
    decision_id: str
    analysis_run_id: str
    rule_revision: str
    requested_policy_revision: str
    evaluated_policy_revision: str | None
    evidence_level: Level
    organization_ceiling: Level
    product_ceiling: Level = 3
    permitted_level: Level
    action: Literal["report", "recommend", "generate_patch", "open_pr", "auto_merge", "auto_deploy"]
    denied_reasons: tuple[str, ...]
    debt: DebtAssessment
    remediation: RemediationRisk | None
    expected_value: ExpectedValue

    @model_validator(mode="after")
    def bounded_action(self) -> "AutonomyDecision":
        actions = ("report", "recommend", "generate_patch", "open_pr", "auto_merge", "auto_deploy")
        if self.product_ceiling > 3 or self.permitted_level > min(
            self.evidence_level, self.organization_ceiling, self.product_ceiling
        ):
            raise ValueError("autonomy exceeds an evidence, organization, or product ceiling")
        if self.action != actions[self.permitted_level]:
            raise ValueError("action does not match the permitted autonomy level")
        return self


class CostEstimates(RiskContract):
    """Independent estimates supplied by an evidence producer, never inferred from risk points."""

    horizon_days: int = Field(ge=1)
    debt_avoided_hours: Hours | None = None
    remediation_hours: Hours | None = None
    verification_hours: Hours | None = None
    regression_probability: Unit | None = None
    regression_loss_hours: Hours | None = None
    uncertainty_fraction: Unit = 0.25
    sources: dict[str, str] = Field(default_factory=dict)


class RiskSignals(RiskContract):
    """Additional M5 evidence; unknown is different from observed zero."""

    incident_burden: Unit | None = None
    ownership_gap: Unit | None = None
    security_sensitive: bool | None = Field(default=None, strict=True)
    sources: dict[str, str] = Field(default_factory=dict)
    evidence_ids: dict[str, str] = Field(default_factory=dict)
