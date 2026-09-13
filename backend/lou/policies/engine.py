"""Deterministic local policy and a narrow OPA-compatible evaluation boundary."""

import json
from typing import Annotated, Literal, Protocol
from urllib import error, request

from pydantic import BaseModel, ConfigDict, Field

from contracts import ExpectedValue, RemediationRisk

Level = Annotated[int, Field(ge=0, le=5, strict=True)]


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analysis_run_id: str
    requested_revision: str
    evidence_level: Level
    remediation: RemediationRisk | None
    expected_value: ExpectedValue
    verification_statuses: tuple[Literal["passed", "failed", "inconclusive"], ...]


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision: str
    max_autonomy: Level
    deny_reasons: tuple[str, ...] = ()
    denied: bool = False


class PolicyEvaluator(Protocol):
    def evaluate(self, inputs: PolicyInput) -> PolicyResult: ...


class LocalPolicy:
    """A testable organizational ceiling; hard denials only lower autonomy."""

    def __init__(
        self,
        *,
        revision: str,
        max_autonomy: int = 3,
        overrides: dict[str, int] | None = None,
        deny_on: frozenset[str] = frozenset(),
    ) -> None:
        if not 0 <= max_autonomy <= 5:
            raise ValueError("organizational ceiling must be A0 through A5")
        if any(not 0 <= value <= 5 for value in (overrides or {}).values()):
            raise ValueError("override ceilings must be A0 through A5")
        self.revision = revision
        self.max_autonomy = max_autonomy
        self.overrides = overrides or {}
        self.deny_on = deny_on

    def evaluate(self, inputs: PolicyInput) -> PolicyResult:
        reasons: list[str] = []
        ceiling = self.max_autonomy
        if inputs.requested_revision != self.revision:
            reasons.append("policy_revision_mismatch")
            ceiling = 0
        if inputs.remediation is None:
            reasons.append("remediation_unassessed")
            ceiling = min(ceiling, 1)
        else:
            for flag in inputs.remediation.hard_risk_flags:
                reasons.append(flag)
                ceiling = min(ceiling, 1)
        if not inputs.verification_statuses or any(
            status != "passed" for status in inputs.verification_statuses
        ):
            reasons.append("verification_not_passed")
            ceiling = min(ceiling, 2)
        if inputs.expected_value.status != "estimated":
            reasons.append("expected_value_unknown")
            ceiling = min(ceiling, 2)
        elif inputs.expected_value.lower_hours is None or inputs.expected_value.lower_hours <= 0:
            reasons.append("expected_value_not_positive_under_uncertainty")
            ceiling = min(ceiling, 1)
        flags = set(inputs.remediation.hard_risk_flags) if inputs.remediation else set()
        for flag, override_ceiling in sorted(self.overrides.items()):
            if flag in flags:
                reasons.append(f"organizational_override:{flag}")
                ceiling = min(ceiling, override_ceiling)
        denied = bool(flags & self.deny_on)
        if denied:
            reasons.extend(f"organizational_deny:{flag}" for flag in sorted(flags & self.deny_on))
            ceiling = 0
        return PolicyResult(
            revision=self.revision,
            max_autonomy=ceiling,
            deny_reasons=tuple(reasons),
            denied=denied,
        )


class OpaPolicy:
    """Evaluate the same JSON input through OPA's data API; malformed replies deny."""

    def __init__(self, *, url: str, revision: str, timeout_seconds: float = 2.0) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("OPA URL must be HTTP(S)")
        self.url = url
        self.revision = revision
        self.timeout_seconds = timeout_seconds

    def evaluate(self, inputs: PolicyInput) -> PolicyResult:
        if inputs.requested_revision != self.revision:
            return PolicyResult(
                revision=self.revision, max_autonomy=0, deny_reasons=("policy_revision_mismatch",)
            )
        payload = json.dumps(
            {"input": inputs.model_dump(mode="json")}, sort_keys=True, separators=(",", ":")
        ).encode()
        call = request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with request.urlopen(call, timeout=self.timeout_seconds) as response:
                data = json.load(response)
            result = PolicyResult.model_validate(data["result"])
            if result.revision != self.revision:
                raise ValueError("OPA returned a different policy revision")
            return result
        except (OSError, error.URLError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return PolicyResult(
                revision=self.revision, max_autonomy=0, deny_reasons=("policy_evaluation_failed",)
            )
