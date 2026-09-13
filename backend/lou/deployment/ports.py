"""Trusted boundary for an external deployment system."""

from __future__ import annotations

from typing import Protocol

from contracts import Release


class DeploymentPort(Protocol):
    """The only boundary permitted to invoke a deployment controller."""

    def release(self, release: Release) -> None: ...

    def promote(self, release: Release) -> None: ...

    def pause(self, release: Release) -> None: ...

    def rollback(self, release: Release) -> None: ...
