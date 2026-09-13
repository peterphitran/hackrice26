from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from lou.core.settings import Settings
from lou.deployment import (
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    InMemoryTraceLookup,
    InMemoryVerificationLookup,
    TraceFact,
    VerificationFact,
)

ANALYSIS_RUN_ID = "00000000-0000-0000-0000-000000000001"
COMMIT_SHA = "a" * 40
TRACE_ID = "0" * 32


def _client(tmp_path: Path, *, verified: bool = True, traced: bool = True) -> TestClient:
    lookup = InMemoryVerificationLookup()
    if verified:
        lookup.record(VerificationFact("verification-1", ANALYSIS_RUN_ID, COMMIT_SHA, "passed"))
    traces = InMemoryTraceLookup()
    if traced:
        traces.record(TraceFact(TRACE_ID, ANALYSIS_RUN_ID, COMMIT_SHA, 12))
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        verifications=lookup,
        traces=traces,
    )
    return TestClient(
        create_app(
            settings=Settings(artifact_root=tmp_path), deployment_service_factory=lambda: service
        )
    )


def test_deployment_api_promotes_a_complete_healthy_canary(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        f"/analysis/{ANALYSIS_RUN_ID}/deployments",
        json={
            "release_id": "release-api",
            "repository_id": "demo",
            "commit_sha": COMMIT_SHA,
            "verification_run_ids": ["verification-1"],
            "trace_ids": ["0" * 32],
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "promoted"
    assert client.get("/deployments/release-api").json()["evidence_count"] == 4
    report = client.get("/deployments/release-api/report")
    assert report.status_code == 200
    assert report.json()["release"]["commit_sha"] == COMMIT_SHA


def test_deployment_api_rolls_back_a_bad_canary_and_supports_manual_rollback(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    created = client.post(
        f"/analysis/{ANALYSIS_RUN_ID}/deployments",
        json={
            "release_id": "release-api-bad",
            "repository_id": "demo",
            "commit_sha": COMMIT_SHA,
            "error_rate": 0.2,
            "verification_run_ids": ["verification-1"],
            "trace_ids": ["0" * 32],
        },
    )

    assert created.status_code == 202
    assert created.json()["status"] == "rolled_back"
    repeated = client.post(
        "/deployments/release-api-bad/rollback", json={"reason": "operator review"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "rolled_back"


def test_deployment_api_pauses_when_health_is_asserted_without_linked_evidence(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        f"/analysis/{ANALYSIS_RUN_ID}/deployments",
        json={"release_id": "unlinked", "repository_id": "demo", "commit_sha": COMMIT_SHA},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "paused"


def test_deployment_api_pauses_when_the_named_verification_run_is_not_recorded(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, verified=False)

    response = client.post(
        f"/analysis/{ANALYSIS_RUN_ID}/deployments",
        json={
            "release_id": "fabricated",
            "repository_id": "demo",
            "commit_sha": COMMIT_SHA,
            "verification_run_ids": ["verification-1"],
            "trace_ids": [TRACE_ID],
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "paused"


def test_deployment_api_pauses_when_the_named_trace_is_not_recorded(tmp_path: Path) -> None:
    client = _client(tmp_path, traced=False)

    response = client.post(
        f"/analysis/{ANALYSIS_RUN_ID}/deployments",
        json={
            "release_id": "untraced",
            "repository_id": "demo",
            "commit_sha": COMMIT_SHA,
            "verification_run_ids": ["verification-1"],
            "trace_ids": [TRACE_ID],
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "paused"
