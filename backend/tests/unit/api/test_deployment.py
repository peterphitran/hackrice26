from fastapi.testclient import TestClient

from apps.api.main import create_app
from lou.core.settings import Settings


def test_deployment_api_promotes_a_complete_healthy_canary(tmp_path) -> None:
    client = TestClient(create_app(settings=Settings(artifact_root=tmp_path)))

    response = client.post(
        "/analysis/00000000-0000-0000-0000-000000000001/deployments",
        json={
            "release_id": "release-api",
            "repository_id": "demo",
            "commit_sha": "a" * 40,
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "promoted"
    assert client.get("/deployments/release-api").json()["evidence_count"] == 4
    report = client.get("/deployments/release-api/report")
    assert report.status_code == 200
    assert report.json()["release"]["commit_sha"] == "a" * 40


def test_deployment_api_rolls_back_a_bad_canary_and_supports_manual_rollback(tmp_path) -> None:
    client = TestClient(create_app(settings=Settings(artifact_root=tmp_path)))
    created = client.post(
        "/analysis/00000000-0000-0000-0000-000000000001/deployments",
        json={
            "release_id": "release-api-bad",
            "repository_id": "demo",
            "commit_sha": "a" * 40,
            "error_rate": 0.2,
        },
    )

    assert created.status_code == 202
    assert created.json()["status"] == "rolled_back"
    repeated = client.post(
        "/deployments/release-api-bad/rollback", json={"reason": "operator review"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "rolled_back"
