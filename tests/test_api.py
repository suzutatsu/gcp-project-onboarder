import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_single_channel_direct_adaptive_card():
    """When ADMIN_WEBHOOK_URL is empty, returns Adaptive Card directly in HTTP response (1 post)."""
    original_admin_url = settings.admin_webhook_url
    try:
        settings.admin_webhook_url = ""
        payload = {
            "type": "message",
            "text": "@GCP Onboarder group-dev@company.com に user1@company.com を追加して",
            "from": {"name": "Unique Test User 1"}
        }

        response = client.post("/webhook", json=payload)
        assert response.status_code == 200
        res_data = response.json()
        assert "attachments" in res_data
        assert res_data["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    finally:
        settings.admin_webhook_url = original_admin_url


def test_webhook_multi_channel_receipt_and_deduplication():
    """When ADMIN_WEBHOOK_URL is configured, returns receipt text response and handles duplicate retry."""
    original_admin_url = settings.admin_webhook_url
    try:
        settings.admin_webhook_url = "https://outlook.office.com/webhook/test-admin"
        payload = {
            "type": "message",
            "text": "@GCP Onboarder group-unique-dedup@company.com に dedupuser@company.com を追加して",
            "from": {"name": "Dedup Test User"}
        }

        # First request -> Receives receipt message
        res1 = client.post("/webhook", json=payload)
        assert res1.status_code == 200
        assert "✅ **申請受付完了**" in res1.json()["text"]

        # Immediate duplicate request -> Returns Zero-Width Space invisible payload
        res2 = client.post("/webhook", json=payload)
        assert res2.status_code == 200
        assert res2.json()["text"] == "\u200b"
    finally:
        settings.admin_webhook_url = original_admin_url


def test_invalid_hmac_production():
    original_token = settings.teams_security_token
    try:
        settings.teams_security_token = "valid_production_secret_token_base64"
        payload = {"text": "hello"}
        response = client.post("/webhook", json=payload, headers={"Authorization": "HMAC invalid_token"})
        assert response.status_code == 401
    finally:
        settings.teams_security_token = original_token
