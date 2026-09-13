"""Agent configuration using Lou's local-first settings conventions."""

from __future__ import annotations

from typing import Literal

from lou.core.settings import Settings


class AgentSettings(Settings):
    """Extend core settings without changing other teams' modules."""

    agent_provider: Literal["mock", "gemini"] = "mock"
    agent_model: str = "gemini-3.8-flash"


def get_agent_settings() -> AgentSettings:
    """Read selection at adapter construction so tests can set isolated environments."""
    return AgentSettings()
