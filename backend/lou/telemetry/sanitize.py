"""Strictly bounded, allowlisted runtime telemetry attributes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_ALLOWED = frozenset(
    {
        "lou.analysis_run_id",
        "lou.repository_id",
        "lou.commit_sha",
        "lou.phase",
        "lou.workload_id",
        "lou.verification_run_id",
        "lou.artifact_id",
        "lou.entrypoint",
        "lou.workload_type",
        "lou.sandbox_kind",
        "lou.command_label",
        "lou.exit_status",
        "lou.classification",
        "lou.mapping_method",
        "lou.correlation_status",
        "db.operation",
        "db.table",
        "db.rows",
        "error.type",
    }
)
_SENSITIVE = (
    "authorization",
    "cookie",
    "token",
    "password",
    "secret",
    "body",
    "sql",
    "query",
    "env",
)


@dataclass(frozen=True)
class SanitizedAttributes:
    values: dict[str, str | int | float | bool]
    redaction_count: int
    limited: bool = False


def sanitize_attributes(
    values: dict[str, Any],
    *,
    max_count: int = 24,
    max_value_bytes: int = 256,
) -> SanitizedAttributes:
    """Keep only approved scalar diagnostics, never raw inputs or source text."""

    output: dict[str, str | int | float | bool] = {}
    redactions = 0
    for key in sorted(values):
        normalized = key.lower()
        value = values[key]
        if key not in _ALLOWED or any(marker in normalized for marker in _SENSITIVE):
            redactions += 1
            continue
        if not isinstance(value, (str, int, float, bool)):
            redactions += 1
            continue
        if len(str(value).encode("utf-8")) > max_value_bytes:
            redactions += 1
            continue
        if len(output) >= max_count:
            return SanitizedAttributes(output, redactions + 1, limited=True)
        output[key] = value
    return SanitizedAttributes(output, redactions)
