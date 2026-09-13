"""Bounded, resumable diagnosis, patch proposal, verification, and decision."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    AnalysisJob,
    Finding,
    LouDecision,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents.context import AgentContextBundle, build_context_bundle
from lou.agents.patch_validation import PatchValidationResult, validate_patch
from lou.agents.provider import AgentAdapter, AgentProvider, ProviderRequest, ProviderResponse
from lou.decision import decide_autonomy
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs

Stage = Literal["context", "diagnose", "patch", "validate", "verify", "decide", "stopped"]
TerminationReason = Literal[
    "verified",
    "max_attempts",
    "time_budget_exceeded",
    "token_budget_exceeded",
    "cost_budget_exceeded",
    "duplicate_patch",
    "decision_declined",
]
Verdict = Literal["passed", "failed", "inconclusive"]


class OrchestrationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, ge=1)
    max_wall_seconds: float = Field(default=120.0, gt=0)
    max_tokens: int = Field(default=20_000, ge=0)
    max_cost_usd: float = Field(default=1.0, ge=0)


class OrchestrationInputs(BaseModel):
    """Trusted records reused when a saved state is resumed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job: AnalysisJob
    context: RepositoryContext
    finding: Finding
    candidate_verification: VerificationResult
    workloads: tuple[WorkloadSelection, ...]
    expected_patch: PatchArtifact
    debt_inputs: DebtInputs
    remediation_inputs: RemediationInputs
    policy: AutonomyPolicy
    allowed_repository_root: Path
    live_sources: tuple[str, ...] = ()

    def required_workload_ids(self) -> tuple[str, ...]:
        planned = self.job.verification_plan.get("workloads")
        if (
            not isinstance(planned, list)
            or not planned
            or not all(isinstance(item, str) and item for item in planned)
        ):
            raise ValueError("The trusted verification plan must list workload IDs")
        if len(set(planned)) != len(planned):
            raise ValueError("The trusted verification plan contains duplicate workloads")
        return tuple(planned)

    def fingerprint(self) -> str:
        return sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class ValidatedPatch(BaseModel):
    """The complete, accepted proposal sent to one independent workload check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patch_diff: str
    patch_artifact: PatchArtifact
    validation: PatchValidationResult
    analysis_job: AnalysisJob
    allowed_repository_root: Path
    workload_id: str
    verification_attempt_id: str
    attempt_count: int = Field(ge=1)

    @model_validator(mode="after")
    def check_identity(self) -> ValidatedPatch:
        if not self.validation.valid or self.validation.reasons or not self.validation.files:
            raise ValueError("Verifier input must contain an accepted patch validation")
        if self.patch_artifact.analysis_run_id != self.analysis_job.analysis_run_id:
            raise ValueError("Patch and analysis run IDs differ")
        if self.patch_artifact.base_commit_sha != self.analysis_job.candidate_commit_sha:
            raise ValueError("Patch base commit differs from the analysis candidate")
        if sha256(self.patch_diff.encode("utf-8")).hexdigest() != self.patch_artifact.patch_sha256:
            raise ValueError("Verifier input patch hash differs from its bytes")
        return self


class Verifier(Protocol):
    """Engineer 3 can implement this without changes to the state machine."""

    def verify(self, patch: ValidatedPatch) -> VerificationResult: ...


class DeterministicMockVerifier:
    """Returns configured verdicts; it never applies or tests a proposed patch."""

    def __init__(
        self,
        *,
        fix_commit_sha: str,
        verdicts_by_attempt: Sequence[Verdict] = ("passed",),
    ) -> None:
        if not verdicts_by_attempt:
            raise ValueError("At least one mock verdict is required")
        self.fix_commit_sha = fix_commit_sha
        self.verdicts_by_attempt = tuple(verdicts_by_attempt)
        self.calls: list[ValidatedPatch] = []

    def verify(self, patch: ValidatedPatch) -> VerificationResult:
        self.calls.append(patch)
        index = min(patch.attempt_count - 1, len(self.verdicts_by_attempt) - 1)
        return VerificationResult(
            verification_run_id=f"{patch.verification_attempt_id}:{patch.workload_id}",
            analysis_run_id=patch.analysis_job.analysis_run_id,
            phase="fix",
            commit_sha=self.fix_commit_sha,
            status=self.verdicts_by_attempt[index],
            workload_id=patch.workload_id,
            metadata={
                "patch_sha256": patch.patch_artifact.patch_sha256,
                "verification_attempt_id": patch.verification_attempt_id,
                "mock_verifier": True,
            },
        )


class AttemptOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int
    outcome: str
    patch_sha256: str | None = None


class OrchestrationState(BaseModel):
    """A complete, JSON-serializable stage-boundary snapshot."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    analysis_run_id: str
    inputs_fingerprint: str
    limits: OrchestrationLimits
    started_at_epoch: float
    stage: Stage = "context"
    attempt_count: int = 0
    elapsed_seconds: float = 0.0
    tokens_spent: int = 0
    estimated_cost_spent_usd: float = 0.0
    prior_patch_hashes: list[str] = Field(default_factory=list)
    current_bundle: AgentContextBundle | None = None
    current_diagnosis: ProviderResponse | None = None
    current_patch_response: ProviderResponse | None = None
    current_validation: PatchValidationResult | None = None
    current_verification_results: list[VerificationResult] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    verification_cursor: int = 0
    last_failure_reason: str | None = None
    attempt_outcomes: list[AttemptOutcome] = Field(default_factory=list)
    pending_termination_reason: TerminationReason | None = None
    termination_reason: TerminationReason | None = None
    decision: LouDecision | None = None


class RemediationOrchestrator:
    """Advance one persisted boundary at a time, using only external verdicts."""

    def __init__(
        self,
        inputs: OrchestrationInputs,
        *,
        verifier: Verifier,
        provider: AgentProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        inputs.required_workload_ids()
        self.inputs = inputs
        self.provider = AgentAdapter(provider)
        self.verifier = verifier
        self.clock = clock

    def start(self, limits: OrchestrationLimits | None = None) -> OrchestrationState:
        return OrchestrationState(
            analysis_run_id=self.inputs.job.analysis_run_id,
            inputs_fingerprint=self.inputs.fingerprint(),
            limits=limits or OrchestrationLimits(),
            started_at_epoch=self.clock(),
        )

    def _elapsed(self, state: OrchestrationState) -> None:
        state.elapsed_seconds = max(state.elapsed_seconds, self.clock() - state.started_at_epoch)

    @staticmethod
    def _budget_reason(state: OrchestrationState) -> TerminationReason | None:
        if state.elapsed_seconds > state.limits.max_wall_seconds:
            return "time_budget_exceeded"
        if state.tokens_spent > state.limits.max_tokens:
            return "token_budget_exceeded"
        if state.estimated_cost_spent_usd > state.limits.max_cost_usd:
            return "cost_budget_exceeded"
        return None

    def _account(self, state: OrchestrationState, response: ProviderResponse) -> None:
        state.tokens_spent += response.tokens_used
        state.estimated_cost_spent_usd += response.estimated_cost_usd
        self._elapsed(state)
        reason = self._budget_reason(state)
        if reason is not None:
            state.pending_termination_reason = reason
            state.stage = "decide"

    def _attempt_id(self, state: OrchestrationState) -> str:
        return f"{state.analysis_run_id}:attempt:{state.attempt_count}"

    def _decision(
        self,
        state: OrchestrationState,
        results: Sequence[VerificationResult],
        *,
        final: bool,
    ) -> LouDecision:
        proposal = state.current_patch_response
        accepted = state.current_validation is not None and state.current_validation.valid
        patch = proposal.patch_artifact if proposal is not None and accepted else None
        patch_content = (
            proposal.patch_diff.encode("utf-8")
            if proposal is not None and proposal.patch_diff is not None and accepted
            else None
        )
        commits = {result.commit_sha for result in state.current_verification_results}
        fix_commit = next(iter(commits)) if len(commits) == 1 else None
        return decide_autonomy(
            decision_id=f"{state.analysis_run_id}:decision:{state.attempt_count}"
            + (":final" if final else ":attempt"),
            analysis_run_id=state.analysis_run_id,
            debt_inputs=self.inputs.debt_inputs,
            remediation_inputs=self.inputs.remediation_inputs,
            policy=self.inputs.policy,
            patch=patch,
            patch_content=patch_content,
            analysis_job=self.inputs.job,
            expected_fix_commit_sha=fix_commit,
            expected_verification_attempt_id=(
                self._attempt_id(state) if state.current_verification_results else None
            ),
            required_workload_ids=self.inputs.required_workload_ids(),
            verification_results=results,
            candidate_regression=self.inputs.candidate_verification,
        )

    def _finish(self, state: OrchestrationState, reason: TerminationReason) -> OrchestrationState:
        state.termination_reason = reason
        state.stage = "stopped"
        state.decision = self._decision(state, state.verification_results, final=True)
        return state

    def step(self, previous: OrchestrationState) -> OrchestrationState:
        """Perform exactly one stage and return a fresh snapshot."""
        if previous.analysis_run_id != self.inputs.job.analysis_run_id or (
            previous.inputs_fingerprint != self.inputs.fingerprint()
        ):
            raise ValueError("Resume inputs differ from the saved orchestration state")
        state = previous.model_copy(deep=True)
        if state.stage == "stopped":
            return state
        self._elapsed(state)
        if state.stage != "decide":
            reason = self._budget_reason(state)
            if reason is not None:
                state.pending_termination_reason = reason
                state.stage = "decide"
                return state

        if state.stage == "context":
            state.attempt_count += 1
            state.current_bundle = build_context_bundle(
                self.inputs.job,
                self.inputs.context,
                self.inputs.finding,
                self.inputs.candidate_verification,
                self.inputs.workloads,
                self.inputs.expected_patch,
                live_sources=self.inputs.live_sources,
            )
            state.current_diagnosis = None
            state.current_patch_response = None
            state.current_validation = None
            state.current_verification_results = []
            state.verification_cursor = 0
            state.last_failure_reason = None
            state.stage = "diagnose"
            return state

        if state.stage in {"diagnose", "patch"}:
            assert state.current_bundle is not None
            operation: Literal["diagnose", "patch"] = (
                "diagnose" if state.stage == "diagnose" else "patch"
            )
            remaining = max(0.001, state.limits.max_wall_seconds - state.elapsed_seconds)
            response = self.provider.call(
                ProviderRequest(
                    operation=operation,
                    bundle=state.current_bundle,
                    timeout_seconds=min(5.0, remaining),
                )
            )
            if operation == "diagnose":
                state.current_diagnosis = response
            else:
                state.current_patch_response = response
            self._account(state, response)
            if response.result.status != "succeeded":
                state.last_failure_reason = f"{operation}_{response.result.status}"
                state.stage = "decide"
            elif operation == "diagnose":
                if state.stage != "decide":
                    state.stage = "patch"
            elif response.patch_diff is None or response.patch_artifact is None:
                state.last_failure_reason = "patch_missing"
                state.stage = "decide"
            elif state.stage != "decide":
                state.stage = "validate"
            return state

        if state.stage == "validate":
            proposal = state.current_patch_response
            assert proposal is not None
            assert proposal.patch_diff is not None and proposal.patch_artifact is not None
            state.current_validation = validate_patch(
                proposal.patch_diff,
                proposal.patch_artifact,
                expected_base_commit_sha=self.inputs.job.candidate_commit_sha,
                allowed_repository_root=self.inputs.allowed_repository_root,
            )
            try:
                actual_hash = sha256(proposal.patch_diff.encode("utf-8")).hexdigest()
            except UnicodeError:
                actual_hash = None
            if actual_hash is not None:
                if actual_hash in state.prior_patch_hashes:
                    state.last_failure_reason = "duplicate_patch"
                    state.pending_termination_reason = "duplicate_patch"
                    state.stage = "decide"
                    return state
                state.prior_patch_hashes.append(actual_hash)
            if proposal.patch_artifact.analysis_run_id != state.analysis_run_id:
                state.last_failure_reason = "patch_run_mismatch"
                state.stage = "decide"
            elif not state.current_validation.valid:
                state.last_failure_reason = "patch_rejected"
                state.stage = "decide"
            else:
                state.stage = "verify"
            return state

        if state.stage == "verify":
            required = self.inputs.required_workload_ids()
            if state.verification_cursor >= len(required):
                state.stage = "decide"
                return state
            proposal = state.current_patch_response
            validation = state.current_validation
            assert proposal is not None and proposal.patch_diff is not None
            assert proposal.patch_artifact is not None and validation is not None
            request = ValidatedPatch(
                patch_diff=proposal.patch_diff,
                patch_artifact=proposal.patch_artifact,
                validation=validation,
                analysis_job=self.inputs.job,
                allowed_repository_root=self.inputs.allowed_repository_root,
                workload_id=required[state.verification_cursor],
                verification_attempt_id=self._attempt_id(state),
                attempt_count=state.attempt_count,
            )
            try:
                result = self.verifier.verify(request)
            except Exception as error:
                state.last_failure_reason = f"verifier_error:{type(error).__name__}"
                state.stage = "decide"
                self._elapsed(state)
                return state
            state.current_verification_results.append(result)
            state.verification_results.append(result)
            state.verification_cursor += 1
            if (
                result.analysis_run_id != state.analysis_run_id
                or result.phase != "fix"
                or result.workload_id != request.workload_id
                or result.metadata.get("patch_sha256") != request.patch_artifact.patch_sha256
                or result.metadata.get("verification_attempt_id") != request.verification_attempt_id
            ):
                state.last_failure_reason = "verifier_result_mismatch"
            elif result.status != "passed" and state.last_failure_reason is None:
                state.last_failure_reason = f"verification_{result.status}"
            self._elapsed(state)
            reason = self._budget_reason(state)
            if reason is not None:
                state.pending_termination_reason = reason
                state.stage = "decide"
            elif state.verification_cursor >= len(required):
                state.stage = "decide"
            return state

        assert state.stage == "decide"
        attempt_decision = self._decision(state, state.current_verification_results, final=False)
        state.decision = attempt_decision
        if state.attempt_count > len(state.attempt_outcomes):
            proposal = state.current_patch_response
            state.attempt_outcomes.append(
                AttemptOutcome(
                    attempt=state.attempt_count,
                    outcome=(
                        state.last_failure_reason
                        or state.pending_termination_reason
                        or "verification_passed"
                    ),
                    patch_sha256=(
                        proposal.patch_artifact.patch_sha256
                        if proposal is not None and proposal.patch_artifact is not None
                        else None
                    ),
                )
            )
        if state.pending_termination_reason is not None:
            return self._finish(state, state.pending_termination_reason)
        required_count = len(self.inputs.required_workload_ids())
        all_passed = (
            len(state.current_verification_results) == required_count
            and all(result.status == "passed" for result in state.current_verification_results)
            and state.last_failure_reason is None
        )
        if all_passed and attempt_decision.autonomy_level >= 2:
            return self._finish(state, "verified")
        if attempt_decision.action != "generate_patch":
            return self._finish(state, "decision_declined")
        if state.attempt_count >= state.limits.max_attempts:
            return self._finish(state, "max_attempts")
        state.stage = "context"
        return state

    def run(
        self,
        state: OrchestrationState | None = None,
        *,
        limits: OrchestrationLimits | None = None,
    ) -> OrchestrationState:
        """Continue from a saved boundary to an explicit terminal reason."""
        current = state if state is not None else self.start(limits)
        while current.stage != "stopped":
            current = self.step(current)
        return current
