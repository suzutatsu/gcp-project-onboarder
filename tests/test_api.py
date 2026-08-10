import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_immediate_response():
    payload = {
        "type": "message",
        "text": "@GCP Onboarder group-unique-1@company.com に user1@company.com を追加して",
        "from": {"name": "Unique Test User 1"}
    }

    # Bypassing HMAC verification when security token is unconfigured
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert "✅ **申請受付完了**" in res_data["text"]


def test_webhook_deduplication():
    payload = {
        "type": "message",
        "text": "@GCP Onboarder group-unique-dedup@company.com に dedupuser@company.com を追加して",
        "from": {"name": "Dedup Test User"}
    }

    # First request -> Receives receipt message
    res1 = client.post("/webhook", json=payload)
    assert res1.status_code == 200
    assert "✅ **申請受付完了**" in res1.json()["text"]

    # Immediate duplicate request from Teams -> Filtered out (returns empty text response, no double message)
    res2 = client.post("/webhook", json=payload)
    assert res2.status_code == 200
    assert res2.json()["text"] == ""


def test_invalid_hmac_production():
    original_token = settings.teams_security_token
    try:
        settings.teams_security_token = "valid_production_secret_token_base64"
        payload = {"text": "hello"}
        response = client.post("/webhook", json=payload, headers={"Authorization": "HMAC invalid_token"})
        assert response.status_code == 401
    finally:
        settings.teams_security_token = original_token
