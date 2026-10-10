"""Tests use no credentials, datasets, GPUs, or external services."""

import pytest
from fastapi.testclient import TestClient

from trinetra import __version__
from trinetra.api.main import create_app
from trinetra.core.config import Settings


@pytest.fixture
def client():
    # Avoid picking up a developer's .env in API contract tests.
    with TestClient(create_app(Settings(_env_file=None, app_name="TRINETRA"))) as client:
        yield client


def test_health_contract(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "service": "TRINETRA", "version": __version__,
    }


def test_openapi_exposes_only_health(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert set(response.json()["paths"]) == {"/api/v1/health"}


def test_analysis_is_not_available(client):
    assert client.post("/api/v1/jobs", json={}).status_code == 404


def test_app_name_can_be_configured(monkeypatch):
    monkeypatch.setenv("TRINETRA_APP_NAME", "TRINETRA local")
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/api/v1/health").json()["service"] == "TRINETRA local"
