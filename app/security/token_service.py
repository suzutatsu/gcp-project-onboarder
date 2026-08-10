import base64
import hmac
import hashlib
import json
import secrets
import time
import logging
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)


class SignedTokenService:
    """
    Stateless / DB-less Signed Token Service.
    Generates and verifies tamper-proof HMAC-SHA256 tokens using a secret key
    generated in volatile RAM at app startup (Ephemeral Memory Key) or injected via SECRET_KEY.
    """

    def __init__(self, secret_key: Optional[str] = None):
        if secret_key:
            self._key = secret_key.encode("utf-8")
        elif settings.secret_key:
            self._key = settings.secret_key.encode("utf-8")
        else:
            # Ephemeral In-Memory Key (generated once in RAM per container startup)
            self._key = secrets.token_bytes(32)
            logger.info("🔑 Ephemeral In-Memory Signing Key initialized in RAM.")

    def create_approval_token(self, request_data: Dict[str, Any], ttl_seconds: Optional[int] = None) -> str:
        """
        Creates a Base64 url-safe signed token string containing request details,
        creation timestamp, expiration timestamp, and HMAC signature.
        """
        now = int(time.time())
        ttl = ttl_seconds if ttl_seconds is not None else settings.token_ttl_seconds
        expires_at = now + ttl

        payload = {
            "req_id": f"REQ-{secrets.token_hex(4).upper()}",
            "iat": now,
            "exp": expires_at,
            "action": request_data.get("action"),
            "group_email": request_data.get("group_email"),
            "member_email": request_data.get("member_email"),
            "requester": request_data.get("requester", "Teams User")
        }

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

        signature = hmac.new(self._key, b64_payload.encode("utf-8"), hashlib.sha256).digest()
        b64_signature = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

        token = f"{b64_payload}.{b64_signature}"
        logger.info(f"Generated signed token (req_id: {payload['req_id']}, exp: {expires_at})")
        return token

    def verify_signed_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verifies token HMAC signature and expiration timestamp.
        """
        try:
            parts = token.split(".")
            if len(parts) != 2:
                logger.warning("Invalid token format (missing dot separator).")
                return None

            b64_payload, b64_signature = parts

            # Recompute signature
            expected_sig = hmac.new(self._key, b64_payload.encode("utf-8"), hashlib.sha256).digest()
            expected_b64_sig = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

            if not hmac.compare_digest(b64_signature, expected_b64_sig):
                logger.warning("🚨 TOKEN TAMPERING DETECTED! HMAC signature mismatch.")
                return None

            # Decode payload
            padding = "=" * (4 - (len(b64_payload) % 4))
            payload_bytes = base64.urlsafe_b64decode(b64_payload + padding)
            payload = json.loads(payload_bytes.decode("utf-8"))

            # Check expiration
            now = int(time.time())
            exp = payload.get("exp", 0)
            if now > exp:
                logger.warning(f"TOKEN EXPIRED! (exp: {exp}, now: {now})")
                return None

            logger.info(f"Signed token verified successfully for req_id '{payload.get('req_id')}'.")
            return payload

        except Exception as e:
            logger.error(f"Failed to verify signed token: {e}", exc_info=True)
            return None


# Global singleton instance
token_service = SignedTokenService()
