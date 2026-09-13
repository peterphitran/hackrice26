"""Gemini uses mocked SDK calls; no test requires a key or network."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from contracts import (
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents import (
    AgentAdapter,
    AgentContextBundle,
    BundleBudget,
    GeminiProposal,
    GeminiProvider,
    ProviderRequest,
    RepositoryText,
    build_context_bundle,
    validate_patch,
)

BACKEND = Path(__file__).parents[3]
FIXTURES = BACKEND / "contracts" / "fixtures" / "demo_checkout"
BROKEN_STORE = BACKEND / "fixtures" / "broken-store"


def _load(name: str, model: type[BaseModel]) -> Any:
    return model.model_validate_json((FIXTURES / name).read_text())


def _bundle(source: str | None = None) -> AgentContextBundle:
    repository_texts = (
        [RepositoryText(kind="file_content", path="store/app.py", text=source)]
        if source is not None
        else []
    )
    return build_context_bundle(
        _load("analysis_job.json", AnalysisJob),
        _load("repository_context.json", RepositoryContext),
        _load("finding.json", Finding),
        _load("verification_candidate.json", VerificationResult),
        [
            _load("workload_pytest.json", WorkloadSelection),
            _load("workload_k6.json", WorkloadSelection),
        ],
        _load("patch_artifact.json", PatchArtifact),
        repository_texts=repository_texts,
        budget=BundleBudget(max_bytes=100_000, max_tokens=40_000),
    )


class _Interactions:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def _response(proposal: GeminiProposal | str, *, status: str = "completed") -> Any:
    text = proposal if isinstance(proposal, str) else proposal.model_dump_json()
    return SimpleNamespace(
        status=status,
        output_text=text,
        usage=SimpleNamespace(total_tokens=135, estimated_cost_usd=None),
    )


def _client(monkeypatch: pytest.MonkeyPatch, output: Any) -> _Interactions:
    interactions = _Interactions(output)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-valid-key-for-mocked-client")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        "lou.agents.live_provider.genai.Client",
        lambda: SimpleNamespace(interactions=interactions),
    )
    return interactions


def _proposal(source: str, *, target: str = "store/app.py") -> GeminiProposal:
    return GeminiProposal(
        diagnosis="One query runs for each cart item.",
        plan="Load product prices in one batch.",
        target_file=target,
        corrected_source=source,
    )


def _seed(tmp_path: Path) -> tuple[Path, str, str, str]:
    root = tmp_path / "broken-store"
    subprocess.run(
        [sys.executable, str(BROKEN_STORE / "scripts" / "seed_fixture_repo.py"), str(root)],
        check=True,
        capture_output=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    candidate = (root / "store" / "app.py").read_text()
    corrected = (BROKEN_STORE / "template" / "store" / "app.py").read_text()
    return root, commit, candidate, corrected


def test_live_diagnosis_uses_separate_instructions_and_sdk_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interactions = _client(monkeypatch, _response(_proposal("corrected source\n")))
    request = ProviderRequest(operation="diagnose", bundle=_bundle())

    result = GeminiProvider().call(request)

    assert result.result.status == "succeeded"
    assert result.result.diagnosis == "One query runs for each cart item."
    assert result.patch_diff is None
    assert result.tokens_used == 135
    assert result.estimated_cost_usd == 0
    assert result.result.metadata["cost_reported"] is False
    call = interactions.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    assert call["system_instruction"].startswith(request.provider_messages()[0])
    assert call["input"] == request.bundle.provider_data_json()
    assert call["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": GeminiProposal.model_json_schema(),
    }


def test_generated_patch_validates_and_applies_to_real_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, commit, candidate, corrected = _seed(tmp_path)
    interactions = _client(monkeypatch, _response(_proposal(corrected)))
    bundle = _bundle(candidate).model_copy(update={"candidate_commit_sha": commit})

    response = GeminiProvider().call(ProviderRequest(operation="patch", bundle=bundle))

    assert response.result.status == "succeeded"
    assert response.patch_diff is not None
    assert response.patch_artifact is not None
    assert response.patch_diff.startswith("diff --git a/store/app.py b/store/app.py\n")
    assert validate_patch(
        response.patch_diff,
        response.patch_artifact,
        expected_base_commit_sha=commit,
        allowed_repository_root=root,
    ).valid
    subprocess.run(
        ["git", "-C", str(root), "apply", "--check", "-"],
        input=response.patch_diff.encode(),
        check=True,
        capture_output=True,
    )
    assert (root / "store" / "app.py").read_text() == candidate
    assert interactions.calls[0]["input"] == bundle.provider_data_json()


@pytest.mark.parametrize(
    ("target", "corrected", "reason"),
    [
        ("other.py", "different\n", "target_not_in_evidence_bundle"),
        ("../store/app.py", "different\n", "target_escapes_repository_root"),
        ("store/app.py", "", "corrected_source_empty"),
        ("store/app.py", "same\n", "corrected_source_unchanged"),
    ],
)
def test_bad_corrected_source_is_abandoned_before_diff(
    monkeypatch: pytest.MonkeyPatch, target: str, corrected: str, reason: str
) -> None:
    _client(monkeypatch, _response(_proposal(corrected, target=target)))
    response = GeminiProvider().call(ProviderRequest(operation="patch", bundle=_bundle("same\n")))
    assert response.result.status == "abandoned"
    assert response.result.metadata["abandon_reason"] == reason
    assert response.patch_diff is None


def test_schema_failure_and_truncated_or_empty_output_are_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ProviderRequest(operation="diagnose", bundle=_bundle())
    for output, reason in [
        (_response('{"diagnosis":"only one field"}'), "response_schema_invalid"),
        (_response(""), "response_empty"),
        (_response("partial", status="incomplete"), "response_truncated"),
    ]:
        _client(monkeypatch, output)
        assert GeminiProvider().call(request).result.metadata["abandon_reason"] == reason


class _QuotaExhausted(Exception):
    """Any SDK error carrying an HTTP 429, however the SDK spells its class name."""

    status_code = 429


class _Unauthorized(Exception):
    status_code = 401


def test_sdk_rate_limit_and_network_failures_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Matched on error shape rather than SDK class identity: google-genai moved its
    # exception classes between 1.x and 2.x, and the mapping must survive that.
    request = ProviderRequest(operation="diagnose", bundle=_bundle())
    for error, reason in [
        (_QuotaExhausted("quota exhausted"), "rate_limit_or_quota_exhausted"),
        (_Unauthorized("bad key"), "invalid_api_key"),
        (ConnectionError("connection reset"), "network_failure"),
        (TimeoutError("deadline exceeded"), "provider_timeout"),
    ]:
        _client(monkeypatch, error)
        assert GeminiProvider().call(request).result.metadata["abandon_reason"] == reason


def test_live_selection_without_key_falls_back_to_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("LOU_AGENT_PROVIDER", "gemini")
    response = AgentAdapter().call(ProviderRequest(operation="diagnose", bundle=_bundle()))
    assert response.result.status == "succeeded"
    assert response.result.metadata["provider"] == "deterministic-mock"
    assert response.result.metadata["fallback_used"] is True
    assert response.result.metadata["fallback_reason"] == "api_key_missing"


def test_live_selection_falls_back_when_quota_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client(monkeypatch, _QuotaExhausted("quota exhausted"))
    monkeypatch.setenv("LOU_AGENT_PROVIDER", "gemini")

    response = AgentAdapter().call(ProviderRequest(operation="patch", bundle=_bundle()))

    assert response.result.status == "succeeded"
    assert response.result.metadata["provider"] == "deterministic-mock"
    assert response.result.metadata["fallback_used"] is True
    assert response.result.metadata["fallback_reason"] == "rate_limit_or_quota_exhausted"


def test_google_key_precedence_and_output_list_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-valid-key-for-mocked-client")
    monkeypatch.setenv("GOOGLE_API_KEY", " ")
    assert (
        GeminiProvider()
        .call(ProviderRequest(operation="diagnose", bundle=_bundle()))
        .result.metadata["abandon_reason"]
        == "invalid_api_key"
    )
    interaction = SimpleNamespace(
        status="completed",
        outputs=[SimpleNamespace(text=_proposal("source\n").model_dump_json())],
        usage=None,
    )
    _client(monkeypatch, interaction)
    response = GeminiProvider().call(ProviderRequest(operation="diagnose", bundle=_bundle()))
    assert response.result.status == "succeeded"
    assert response.tokens_used == 0
    assert response.result.metadata["usage_reported"] is False
    assert (
        json.loads(response.result.model_dump_json())["diagnosis"]
        == "One query runs for each cart item."
    )
