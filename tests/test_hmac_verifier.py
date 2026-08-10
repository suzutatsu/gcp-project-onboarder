import base64
import hmac
import hashlib
from app.security.hmac_verifier import verify_teams_signature


def test_verify_teams_signature_valid():
    secret_token = "my_secret_token_12345"
    secret_b64 = base64.b64encode(secret_token.encode("utf-8")).decode("utf-8")
    body = b'{"text": "hello"}'

    # Compute expected signature
    digest = hmac.new(secret_token.encode("utf-8"), body, hashlib.sha256).digest()
    sig_b64 = base64.b64encode(digest).decode("utf-8")

    auth_header = f"HMAC {sig_b64}"

    assert verify_teams_signature(body, auth_header, secret_b64) is True


def test_verify_teams_signature_invalid():
    secret_b64 = base64.b64encode(b"my_secret").decode("utf-8")
    body = b'{"text": "hello"}'
    auth_header = "HMAC invalid_signature_base64"

    assert verify_teams_signature(body, auth_header, secret_b64) is False
