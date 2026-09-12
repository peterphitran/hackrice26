"""Logging configuration shared by application adapters."""

from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    """Configure a concise local console logger once per process."""

    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
