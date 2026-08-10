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
        "text": "@GCP Onboarder group-dev@company.com に yamada@company.com を追加して",
        "from": {"name": "Test User"}
    }

    # Bypassing HMAC verification when security token is unconfigured
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert "✅ **申請受付完了**" in res_data["text"]


def test_invalid_hmac_production():
    original_token = settings.teams_security_token
    try:
        settings.teams_security_token = "valid_production_secret_token_base64"
        payload = {"text": "hello"}
        response = client.post("/webhook", json=payload, headers={"Authorization": "HMAC invalid_token"})
        assert response.status_code == 401
    finally:
        settings.teams_security_token = original_token
