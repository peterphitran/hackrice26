"""Bounded agent context and offline provider adapter."""

from typing import TYPE_CHECKING, Any

from lou.agents.context import (
    AgentContextBundle,
    BundleBudget,
    BundleItem,
    OmittedContext,
    RepositoryText,
    build_context_bundle,
)
from lou.agents.orchestration import (
    DeterministicMockVerifier,
    OrchestrationInputs,
    OrchestrationLimits,
    OrchestrationState,
    RemediationOrchestrator,
    ValidatedPatch,
    Verifier,
)
from lou.agents.patch_validation import (
    ParsedPatchFile,
    PatchRejection,
    PatchValidationLimits,
    PatchValidationResult,
    validate_patch,
)
from lou.agents.provider import (
    AgentAdapter,
    AgentProvider,
    DeterministicMockProvider,
    ProviderRequest,
    ProviderResponse,
)

if TYPE_CHECKING:
    from lou.agents.live_provider import GeminiProposal, GeminiProvider

__all__ = [
    "AgentAdapter",
    "AgentContextBundle",
    "AgentProvider",
    "BundleBudget",
    "BundleItem",
    "DeterministicMockProvider",
    "DeterministicMockVerifier",
    "GeminiProposal",
    "GeminiProvider",
    "OmittedContext",
    "OrchestrationInputs",
    "OrchestrationLimits",
    "OrchestrationState",
    "ParsedPatchFile",
    "PatchRejection",
    "PatchValidationLimits",
    "PatchValidationResult",
    "ProviderRequest",
    "ProviderResponse",
    "RepositoryText",
    "RemediationOrchestrator",
    "ValidatedPatch",
    "Verifier",
    "build_context_bundle",
    "validate_patch",
]


def __getattr__(name: str) -> Any:
    """Load the optional Gemini SDK only when callers explicitly request it."""

    if name in {"GeminiProposal", "GeminiProvider"}:
        from lou.agents import live_provider

        return getattr(live_provider, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
