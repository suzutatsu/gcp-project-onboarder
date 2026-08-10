from app.config import settings
from app.services.llm_parser import parse_request_with_llm


def test_heuristic_parser_add_member():
    text = "@GCP Onboarder 開発チーム (group-dev@company.com) に 山田さん (yamada@company.com) を追加して"
    parsed = parse_request_with_llm(text)

    assert parsed is not None
    assert parsed["action"] == "add_member"
    assert parsed["group_email"] == "group-dev@company.com"
    assert parsed["member_email"] == "yamada@company.com"


def test_heuristic_parser_remove_member():
    text = "@GCP Onboarder 開発チーム (group-dev@company.com) から 山田さん (yamada@company.com) を削除して"
    parsed = parse_request_with_llm(text)

    assert parsed is not None
    assert parsed["action"] == "remove_member"
    assert parsed["group_email"] == "group-dev@company.com"
    assert parsed["member_email"] == "yamada@company.com"


def test_default_group_email_fallback():
    original_default = settings.default_group_email
    try:
        settings.default_group_email = "fixed-default-group@company.com"
        text = "@GCP Onboarder 山田さん (yamada@company.com) を追加して"
        parsed = parse_request_with_llm(text)

        assert parsed is not None
        assert parsed["action"] == "add_member"
        assert parsed["group_email"] == "fixed-default-group@company.com"
        assert parsed["member_email"] == "yamada@company.com"
    finally:
        settings.default_group_email = original_default


def test_default_group_email_parameter_argument():
    text = "@GCP Onboarder 山田さん (yamada@company.com) を追加して"
    parsed = parse_request_with_llm(text, default_group_email="arg-default-group@company.com")

    assert parsed is not None
    assert parsed["action"] == "add_member"
    assert parsed["group_email"] == "arg-default-group@company.com"
    assert parsed["member_email"] == "yamada@company.com"


def test_missing_email_parsing():
    text = "@GCP Onboarder 開発チーム (group-dev@company.com) に 山田さんを追加して"
    parsed = parse_request_with_llm(text)

    assert parsed is not None
    assert parsed["action"] == "add_member"
    assert parsed["member_email"] is None
