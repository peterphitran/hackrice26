"""M7 local collector and collector-outage rehearsal."""

from __future__ import annotations

import json
import sys
from typing import cast
from urllib.error import URLError
from urllib.request import urlopen

from lou.telemetry import OtlpTelemetry, RuntimeCorrelationContext


def _healthy(endpoint: str) -> bool:
    health = endpoint.replace(":4318/v1/traces", ":13133/")
    try:
        with urlopen(health, timeout=2) as response:  # noqa: S310 - explicit local rehearsal endpoint
            return cast(int, response.status) == 200
    except (URLError, OSError):
        return False


def _emit(endpoint: str) -> str:
    telemetry = OtlpTelemetry(endpoint=endpoint, timeout_seconds=2)
    context = RuntimeCorrelationContext(
        "m7-rehearsal", "broken-store", "candidate-revision", "candidate", "checkout-k6"
    )
    telemetry.record(
        "lou.db.query",
        context,
        {"db.operation": "SELECT", "db.table": "broken_store.products", "db.rows": 51},
    )
    status = telemetry.flush()
    telemetry.shutdown()
    return status


def main() -> int:
    endpoint = "http://127.0.0.1:4318/v1/traces"
    healthy = _healthy(endpoint)
    healthy_status = _emit(endpoint) if healthy else "unavailable"
    outage_status = _emit("http://127.0.0.1:1/v1/traces")
    payload = {
        "collector_available": healthy,
        "healthy_exporter_status": healthy_status,
        "outage_exporter_status": outage_status,
        "outage_is_non_blocking": outage_status == "unavailable",
    }
    print("__LOU_M7_RESULT__ " + json.dumps(payload, sort_keys=True))
    return 0 if healthy and healthy_status == "healthy" and outage_status == "unavailable" else 1


if __name__ == "__main__":
    sys.exit(main())
