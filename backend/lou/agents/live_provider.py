"""Gemini diagnosis and byte-exact patch proposals from bounded evidence."""

from __future__ import annotations

import difflib
import os
import time
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from google import genai
from pydantic import BaseModel, ConfigDict, ValidationError

from contracts import AgentResult, PatchArtifact
from lou.agents.context import AgentContextBundle
from lou.agents.provider import ProviderRequest, ProviderResponse, _abandoned
from lou.agents.settings import get_agent_settings


class GeminiProposal(BaseModel):
    """The model supplies complete source; trusted code constructs the diff."""

    model_config = ConfigDict(extra="forbid")

    diagnosis: str
    plan: str
    target_file: str
    corrected_source: str


def _reason(error: Exception) -> str:
    name = type(error).__name__
    status = getattr(error, "status_code", getattr(error, "code", None))
    if name in {"RateLimitError"} or status == 429:
        return "rate_limit_or_quota_exhausted"
    if name in {"AuthenticationError"} or status in {401, 403}:
        return "invalid_api_key"
    if name in {
        "APITimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
    } or isinstance(error, TimeoutError):
        return "provider_timeout"
    if name in {"APIConnectionError", "ConnectError", "NetworkError"} or isinstance(
        error, ConnectionError
    ):
        return "network_failure"
    if name in {"APIResponseValidationError"}:
        return "response_schema_invalid"
    return f"provider_error:{name}"


def _api_key() -> str | None:
    # The SDK gives GOOGLE_API_KEY precedence, so use the same order for preflight.
    if "GOOGLE_API_KEY" in os.environ:
        return os.environ["GOOGLE_API_KEY"]
    return os.environ.get("GEMINI_API_KEY")


def _original_source(bundle: AgentContextBundle, path: str) -> str | None:
    for item in bundle.items:
        payload = item.payload
        if (
            item.trust == "untrusted_repository"
            and payload.get("kind") == "file_content"
            and payload.get("path") == path
            and path in item.file_paths
        ):
            value = payload.get("untrusted_repository_data")
            if isinstance(value, str):
                return value
    return None


def _target_reason(bundle: AgentContextBundle, proposal: GeminiProposal) -> str | None:
    path = proposal.target_file
    pure = PurePosixPath(path)
    if (
        not path
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in path
        or "\x00" in path
        or "\n" in path
        or "\r" in path
    ):
        return "target_escapes_repository_root"
    if not any(path in item.file_paths for item in bundle.items):
        return "target_not_in_evidence_bundle"
    if not proposal.corrected_source:
        return "corrected_source_empty"
    return None


def _diff(path: str, original: str, corrected: str) -> str:
    hunks = difflib.unified_diff(
        original.splitlines(keepends=True),
        corrected.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    )
    diff = [f"diff --git a/{path} b/{path}\n"]
    for line in hunks:
        diff.append(line)
        if not line.endswith("\n"):
            diff.append("\n\\ No newline at end of file\n")
    return "".join(diff)


def _output_text(interaction: Any) -> str | None:
    text = getattr(interaction, "output_text", None)
    if isinstance(text, str):
        return text
    # google-genai 1.75 Interaction exposes text through its outputs list.
    outputs = getattr(interaction, "outputs", None)
    if isinstance(outputs, list):
        parts = [item.text for item in outputs if isinstance(getattr(item, "text", None), str)]
        return "".join(parts)
    return None


def _usage(interaction: Any) -> tuple[int, float, bool, bool]:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return 0, 0.0, False, False
    total = getattr(usage, "total_tokens", None)
    if isinstance(total, int):
        token_count = total
        tokens_reported = True
    else:
        input_tokens = getattr(usage, "total_input_tokens", None)
        output_tokens = getattr(usage, "total_output_tokens", None)
        tokens_reported = isinstance(input_tokens, int) or isinstance(output_tokens, int)
        token_count = (input_tokens if isinstance(input_tokens, int) else 0) + (
            output_tokens if isinstance(output_tokens, int) else 0
        )
    raw_cost = getattr(usage, "estimated_cost_usd", None)
    if isinstance(raw_cost, (int, float)):
        cost = float(raw_cost)
        cost_reported = True
    else:
        cost = 0.0
        cost_reported = False
    return token_count, cost, tokens_reported, cost_reported


class GeminiProvider:
    """Live second implementation of AgentProvider; never edits repository files."""

    name = "gemini"

    def __init__(self) -> None:
        # google-genai 2.x closes a Client once nothing references it, so an inline
        # genai.Client().interactions.create(...) can be collected mid-request.
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = genai.Client()
        return self._client

    def call(self, request: ProviderRequest) -> ProviderResponse:
        start = time.perf_counter()

        def abandon(reason: str) -> ProviderResponse:
            return _abandoned(
                request,
                reason,
                provider=self.name,
                elapsed_ms=(time.perf_counter() - start) * 1_000,
            )

        key = _api_key()
        if key is None:
            return abandon("api_key_missing")
        if not key.strip() or any(character.isspace() for character in key):
            return abandon("invalid_api_key")
        try:
            instructions, evidence = request.provider_messages()
            operation_instruction = (
                "Diagnose the regression. Return the required JSON fields."
                if request.operation == "diagnose"
                else "Repair one evidenced file. Return its complete corrected source in JSON."
            )
            model = get_agent_settings().agent_model
            interaction = self._get_client().interactions.create(
                model=model,
                input=evidence,
                system_instruction=f"{instructions}\n{operation_instruction}",
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": GeminiProposal.model_json_schema(),
                },
                timeout=request.timeout_seconds,
            )
            status = getattr(interaction, "status", "completed")
            if status == "incomplete":
                return abandon("response_truncated")
            if status != "completed":
                return abandon(f"response_status:{status}")
            output_text = _output_text(interaction)
            if not output_text:
                return abandon("response_empty")
            try:
                proposal = GeminiProposal.model_validate_json(output_text)
            except ValidationError:
                return abandon("response_schema_invalid")
            patch_diff: str | None = None
            artifact: PatchArtifact | None = None
            if request.operation == "patch":
                reason = _target_reason(request.bundle, proposal)
                if reason is not None:
                    return abandon(reason)
                original = _original_source(request.bundle, proposal.target_file)
                if original is None:
                    return abandon("original_source_missing")
                if proposal.corrected_source == original:
                    return abandon("corrected_source_unchanged")
                patch_diff = _diff(proposal.target_file, original, proposal.corrected_source)
                added = sum(
                    line.startswith("+") and not line.startswith("+++")
                    for line in patch_diff.splitlines()
                )
                deleted = sum(
                    line.startswith("-") and not line.startswith("---")
                    for line in patch_diff.splitlines()
                )
                artifact = PatchArtifact(
                    patch_id=f"{request.bundle.analysis_run_id}:gemini-proposal",
                    analysis_run_id=request.bundle.analysis_run_id,
                    base_commit_sha=request.bundle.candidate_commit_sha,
                    patch_sha256=sha256(patch_diff.encode("utf-8")).hexdigest(),
                    artifact_uri=f"gemini://{request.bundle.analysis_run_id}/proposal.diff",
                    files_changed=1,
                    lines_added=added,
                    lines_deleted=deleted,
                    metadata={"proposal_only": True, "provider": self.name},
                )
            tokens, cost, usage_reported, cost_reported = _usage(interaction)
            metadata = {
                "provider": self.name,
                "operation": request.operation,
                "model": model,
                "usage_reported": usage_reported,
                "cost_reported": cost_reported,
                "tokens_used": tokens,
                "estimated_cost_usd": cost,
                "omitted_context": [item.key for item in request.bundle.omitted],
            }
            result = AgentResult(
                agent_run_id=f"{request.bundle.analysis_run_id}:{request.operation}:gemini",
                analysis_run_id=request.bundle.analysis_run_id,
                status="succeeded",
                diagnosis=proposal.diagnosis,
                plan=proposal.plan,
                confidence=0.5,
                metadata=metadata,
            )
            return ProviderResponse(
                result=result,
                patch_diff=patch_diff,
                patch_artifact=artifact,
                timeout_seconds=request.timeout_seconds,
                retry_count=0,
                tokens_used=tokens,
                estimated_cost_usd=cost,
                elapsed_ms=(time.perf_counter() - start) * 1_000,
            )
        except Exception as error:
            return abandon(_reason(error))
