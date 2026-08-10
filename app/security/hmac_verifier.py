import base64
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)


def verify_teams_signature(body_bytes: bytes, auth_header: str, secret_token: str) -> bool:
    """
    Verifies Microsoft Teams Outgoing Webhook HMAC-SHA256 signature against timing attacks.

    :param body_bytes: Raw HTTP request body bytes
    :param auth_header: Authorization header value from HTTP request (e.g., "HMAC <sig>")
    :param secret_token: Security token provided by Teams Outgoing Webhook configuration
    :return: True if valid, False otherwise
    """
    if not auth_header or not secret_token:
        logger.warning("[HMAC検証失敗] Authorization ヘッダーまたはセキュリティトークンが未設定です。")
        return False

    # Extract received signature token
    parts = auth_header.strip().split()
    received_signature = parts[1] if len(parts) > 1 else parts[0]

    try:
        # Decode secret token (Teams tokens are base64-encoded strings)
        try:
            # Handle potential padding issues
            padded_token = secret_token + "=" * (-len(secret_token) % 4)
            key_bytes = base64.b64decode(padded_token)
        except Exception:
            key_bytes = secret_token.encode("utf-8")

        # Compute HMAC-SHA256 digest over the raw request body
        computed_digest = hmac.new(key_bytes, body_bytes, hashlib.sha256).digest()
        computed_signature = base64.b64encode(computed_digest).decode("utf-8")

        # Constant-time comparison to prevent timing side-channel attacks
        is_valid = hmac.compare_digest(computed_signature.strip(), received_signature.strip())
        if not is_valid:
            logger.warning(
                f"[HMAC署名不一致] 受信署名長={len(received_signature)}, "
                f"算出署名長={len(computed_signature)}"
            )
        return is_valid
    except Exception as e:
        logger.error(f"[HMAC検証例外] 署名検証中に予期せぬエラーが発生しました: {e}", exc_info=True)
        return False
