"""Tests for zip->state lookup (discovery/UX only, never used in cost estimates)."""

from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.tools.zip_lookup import zip_to_state
from tests.spuf_fixture import patch_settings


def test_zip_to_state_known_prefixes():
    assert zip_to_state("72201") == "AR"
    assert zip_to_state("75201") == "TX"
    assert zip_to_state("33101") == "FL"
    assert zip_to_state("10001") == "NY"


def test_zip_to_state_rejects_invalid_input():
    assert zip_to_state(None) is None
    assert zip_to_state("") is None
    assert zip_to_state("1234") is None
    assert zip_to_state("abcde") is None
    assert zip_to_state("123456") is None


def test_zip_lookup_endpoint_returns_state_for_known_zip():
    client = TestClient(app)
    response = client.get("/api/zip-lookup", params={"zip": "72201"})
    assert response.status_code == 200
    body = response.json()
    assert body["zip"] == "72201"
    assert body["state"] == "AR"


def test_zip_lookup_endpoint_returns_null_state_for_unrecognized_zip():
    client = TestClient(app)
    response = client.get("/api/zip-lookup", params={"zip": "not-a-zip"})
    assert response.status_code == 200
    assert response.json()["state"] is None


def test_states_endpoint_returns_only_ingested_states(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)

    client = TestClient(app)
    response = client.get("/api/states")
    assert response.status_code == 200
    assert response.json() == {"states": ["FL"]}
