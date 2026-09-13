"""Local rehearsal and narrowly-scoped Argo Rollouts adapters."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from contracts import Release
from lou.deployment.ports import DeploymentPort


@dataclass
class InMemoryDeploymentAdapter(DeploymentPort):
    """Deterministic staging adapter used by tests and local rehearsal."""

    actions: list[tuple[str, str]] = field(default_factory=list)

    def release(self, release: Release) -> None:
        self._record("release", release)

    def promote(self, release: Release) -> None:
        self._record("promote", release)

    def pause(self, release: Release) -> None:
        self._record("pause", release)

    def rollback(self, release: Release) -> None:
        self._record("rollback", release)

    def _record(self, action: str, release: Release) -> None:
        if release.target.environment != "staging":
            raise ValueError("the local deployment adapter only permits staging")
        item = (action, release.release_id)
        if item not in self.actions:
            self.actions.append(item)


@dataclass(frozen=True)
class ArgoRolloutsAdapter(DeploymentPort):
    """Thin trusted adapter; it never stores or returns controller credentials."""

    kubectl: str = "kubectl"
    timeout_seconds: int = 30

    def release(self, release: Release) -> None:
        self._run("restart", release)

    def promote(self, release: Release) -> None:
        self._run("promote", release)

    def pause(self, release: Release) -> None:
        self._run("pause", release)

    def rollback(self, release: Release) -> None:
        self._run("abort", release)

    def _run(self, action: str, release: Release) -> None:
        if release.target.environment != "staging":
            raise ValueError("M8 does not permit production deployment")
        command = [
            self.kubectl,
            "argo",
            "rollouts",
            action,
            release.target.service,
            "--namespace",
            release.target.namespace,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=self.timeout_seconds, check=False
        )
        if result.returncode:
            raise RuntimeError("Argo Rollouts command failed")
