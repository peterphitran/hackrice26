"""Bounded local telemetry artifacts and conservative retention cleanup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


def write_artifact(
    root: Path,
    analysis_run_id: str,
    payload: dict[str, object],
    *,
    max_bytes: int,
) -> Path | None:
    """Write one safe debug summary only when it remains within its declared bound."""

    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > max_bytes:
        return None
    directory = root.resolve() / "telemetry"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{analysis_run_id}.json"
    target.write_bytes(raw)
    return target


def cleanup_expired(
    root: Path, *, retention_days: int, now: datetime | None = None
) -> tuple[Path, ...]:
    """Remove expired telemetry files only; Lou evidence lives in PostgreSQL separately."""

    directory = root.resolve() / "telemetry"
    if not directory.is_dir():
        return ()
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    removed: list[Path] = []
    for path in sorted(directory.glob("*.json")):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if modified < cutoff:
            path.unlink(missing_ok=True)
            removed.append(path)
    return tuple(removed)
