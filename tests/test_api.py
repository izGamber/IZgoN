"""Tests for the FastAPI endpoints."""
import json
import pytest
from fastapi.testclient import TestClient
from app import app, init_db, flush_all_state


@pytest.fixture
def client():
    """Create a test client."""
    init_db(":memory:")
    return TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up after each test."""
    yield
    try:
        flush_all_state()
    except Exception:
        pass


class TestSyncEndpoint:
    """Test POST /api/nodes/{node_id}/sync endpoint."""

    def test_missing_api_key(self, client):
        """Test that requests without API key are rejected."""
        response = client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5}}
        )
        assert response.status_code == 401

    def test_invalid_node_id(self, client):
        """Test that invalid node_id is rejected."""
        response = client.post(
            "/api/nodes/invalid@node/sync",
            json={"state": {"temp": 21.5}},
            headers={"X-API-Key": "dev-local-key"}
        )
        assert response.status_code == 422

    def test_valid_sync_request(self, client):
        """Test a valid sync request."""
        response = client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5, "humidity": 60}},
            headers={"X-API-Key": "dev-local-key"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "sensor-01"
        assert "status" in data
        assert "bytes_full" in data
        assert "bytes_sent" in data

    def test_no_change_on_repeat(self, client):
        """Test that repeated identical states return NO_CHANGE."""
        # First sync
        response1 = client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5}},
            headers={"X-API-Key": "dev-local-key"}
        )
        assert response1.status_code == 200

        # Second sync with same state
        response2 = client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5}},
            headers={"X-API-Key": "dev-local-key"}
        )
        assert response2.status_code == 200
        data = response2.json()
        assert data["status"] == "NO_CHANGE"
        assert data["bytes_sent"] == 0

    def test_delta_on_change(self, client):
        """Test that only changed fields are sent."""
        # First sync
        client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5, "humidity": 60}},
            headers={"X-API-Key": "dev-local-key"}
        )

        # Second sync with temperature change
        response = client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 22.1, "humidity": 60}},
            headers={"X-API-Key": "dev-local-key"}
        )
        data = response.json()
        assert data["status"] == "SYNC_REQUIRED"
        assert "temp" in data["delta"]
        assert "humidity" not in data["delta"]


class TestMetricsEndpoint:
    """Test GET /api/metrics endpoint."""

    def test_metrics_available(self, client):
        """Test that metrics endpoint is accessible."""
        response = client.get("/api/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "total_sync_events" in data
        assert "active_nodes" in data
        assert "bandwidth_saved_pct" in data

    def test_metrics_after_syncs(self, client):
        """Test that metrics reflect synced data."""
        # Perform a sync
        client.post(
            "/api/nodes/sensor-01/sync",
            json={"state": {"temp": 21.5}},
            headers={"X-API-Key": "dev-local-key"}
        )

        response = client.get("/api/metrics")
        data = response.json()
        assert data["total_sync_events"] >= 1


class TestHealthEndpoint:
    """Test GET /healthz endpoint."""

    def test_healthz_available(self, client):
        """Test health check endpoint."""
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert "storage" in data
