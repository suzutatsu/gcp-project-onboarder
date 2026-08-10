import pytest
from app.security.guardrails import validate_request_guardrails, GuardrailValidationError


def test_guardrails_valid_add_member():
    valid_request = {
        "action": "add_member",
        "group_email": "dev@company.com",
        "member_email": "yamada@company.com"
    }
    # Should pass without raising exception
    validate_request_guardrails(valid_request)


def test_guardrails_valid_remove_member():
    valid_request = {
        "action": "remove_member",
        "group_email": "dev@company.com",
        "member_email": "yamada@company.com"
    }
    # Should pass without raising exception
    validate_request_guardrails(valid_request)


def test_guardrails_disallowed_domain():
    invalid_request = {
        "action": "add_member",
        "group_email": "dev@company.com",
        "member_email": "hacker@malicious-domain.com"
    }

    with pytest.raises(GuardrailValidationError) as excinfo:
        validate_request_guardrails(
            invalid_request,
            allowed_domains=["company.com"]
        )

    assert "許可されていないメールアドレスドメインです" in str(excinfo.value)
