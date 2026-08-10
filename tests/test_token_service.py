import time
from app.security.token_service import SignedTokenService


def test_ephemeral_memory_key_token_service():
    # Instantiate with no secret_key -> auto-generates ephemeral RAM key
    service = SignedTokenService(secret_key=None)

    request_data = {
        "action": "add_member",
        "group_email": "dev-group@company.com",
        "member_email": "yamada@company.com",
        "requester": "Tanaka"
    }

    # 1. Create token with ephemeral memory key
    token = service.create_approval_token(request_data, ttl_seconds=60)
    assert token is not None
    assert "." in token

    # 2. Decode & verify valid token
    decoded = service.verify_signed_token(token)
    assert decoded is not None
    assert decoded["action"] == "add_member"
    assert decoded["group_email"] == "dev-group@company.com"
    assert decoded["member_email"] == "yamada@company.com"


def test_token_tampering_rejection():
    service = SignedTokenService(secret_key="test_secret_key")
    request_data = {"action": "add_member", "group_email": "dev@company.com", "member_email": "yamada@company.com"}

    token = service.create_approval_token(request_data)
    payload_b64, sig_b64 = token.split(".")

    # Tamper signature
    tampered_token = f"{payload_b64}.tampered_sig"
    assert service.verify_signed_token(tampered_token) is None


def test_token_expiration_rejection():
    service = SignedTokenService(secret_key="test_secret_key")
    request_data = {"action": "add_member", "group_email": "dev@company.com", "member_email": "yamada@company.com"}

    # Create token with -1 TTL (expired immediately)
    token = service.create_approval_token(request_data, ttl_seconds=-1)
    assert service.verify_signed_token(token) is None
