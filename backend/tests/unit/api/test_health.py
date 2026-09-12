from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_returns_application_identity() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "lou", "version": "0.1.0"}
