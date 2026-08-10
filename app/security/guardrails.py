import re
import logging
from typing import Dict, Any, List, Optional
from app.config import settings

logger = logging.getLogger(__name__)

# Allowed Actions
ALLOWED_ACTIONS = {"add_member", "remove_member"}

# Email Address regex pattern (RFC 5322 simplified)
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class GuardrailValidationError(ValueError):
    """Custom exception raised when a security guardrail validation check fails."""
    pass


def validate_request_guardrails(
    request: Dict[str, Any],
    allowed_domains: Optional[List[str]] = None
) -> None:
    """
    Strictly validates all request parameters against Python security guardrails.

    :param request: Parsed request dictionary containing action, group_email, member_email
    :param allowed_domains: Optional list of whitelisted email domains (defaults to settings.allowed_domains)
    :raises GuardrailValidationError: If any safety check fails
    """
    domains_whitelist = allowed_domains if allowed_domains is not None else settings.allowed_domains

    action = request.get("action")
    if not action or action not in ALLOWED_ACTIONS:
        raise GuardrailValidationError(
            f"許可されていないアクションです: '{action}'. (許可アクション: {', '.join(sorted(ALLOWED_ACTIONS))})"
        )

    # 1. Validate Group Email Format (Sanitize non-email strings / nulls & apply DEFAULT_GROUP_EMAIL fallback)
    group_email = request.get("group_email")
    is_valid_group_email = bool(group_email and EMAIL_REGEX.match(str(group_email)))

    if not is_valid_group_email:
        if settings.default_group_email:
            logger.info(f"[ガードレール補正] グループ未指定・非メール形式 ('{group_email}') のため、デフォルトグループ '{settings.default_group_email}' を適用しました。")
            group_email = settings.default_group_email
            request["group_email"] = group_email
        else:
            if not group_email or str(group_email).strip().lower() in ["null", "none", "undefined", ""]:
                request["group_email"] = None
                raise GuardrailValidationError("対象のGoogleグループが判別できませんでした。グループメールアドレス（例: group-dev@company.com）を明記して再度ご依頼ください。")
            else:
                raise GuardrailValidationError(f"無効なグループメールアドレスフォーマットです: '{group_email}'")

    _validate_domain(group_email, domains_whitelist)

    # 2. Validate Member Email
    member_email = request.get("member_email")
    if member_email and str(member_email).strip().lower() in ["null", "none", "undefined", ""]:
        member_email = None
        request["member_email"] = None

    if not member_email:
        raise GuardrailValidationError("対象メンバーのメールアドレスが提示されていません。メールアドレス（例: user@company.com）を含めて再度メッセージを送ってください。")
    if not EMAIL_REGEX.match(member_email):
        raise GuardrailValidationError(f"無効なメンバーメールアドレスフォーマットです: '{member_email}'")
    _validate_domain(member_email, domains_whitelist)

    logger.info(f"[ガードレール検証成功] アクション '{action}' の全検証にパスしました。")


def _validate_domain(email: str, whitelisted_domains: List[str]) -> None:
    """Helper method to validate email domain against whitelist."""
    if not whitelisted_domains:
        return

    domain = email.split("@")[-1].lower()
    if domain not in whitelisted_domains:
        raise GuardrailValidationError(
            f"許可されていないメールアドレスドメインです: '@{domain}'. 許可ドメイン一覧: {whitelisted_domains}"
        )
