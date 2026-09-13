"""Trusted boundary for an external deployment system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from contracts import Release


class DeploymentPort(Protocol):
    """The only boundary permitted to invoke a deployment controller."""

    def release(self, release: Release) -> None: ...

    def promote(self, release: Release) -> None: ...

    def pause(self, release: Release) -> None: ...

    def rollback(self, release: Release) -> None: ...


@dataclass(frozen=True)
class VerificationFact:
    """The recorded identity and outcome of one verification run."""

    verification_run_id: str
    analysis_run_id: str
    commit_sha: str
    status: str


class VerificationLookupPort(Protocol):
    """Read recorded verification runs so a release cannot assert its own health."""

    def get(self, verification_run_id: str) -> VerificationFact | None: ...
