import hashlib
import json
import re
import time
import logging
from typing import Dict, Any, Tuple
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert AI security parser for Google Workspace Group member management.
Analyze the user's natural language request and extract the target parameters into a JSON object with the following fields:

- action: "add_member" | "remove_member"
- group_email: Target Google Group email address (e.g. "group-dev@company.com"). If not mentioned or omitted in the message, set to null.
- member_email: Target user email address (e.g. "yamada@company.com"). If no user email address is specified, set to null.

Extraction Rules:
1. If the request mentions adding a user to a group -> action is "add_member".
2. If the request mentions removing a user from a group -> action is "remove_member".
3. If only a user email address is mentioned and no group email is specified, set group_email to null (the system will automatically use the default group if configured).
4. If an email address is missing in the message, set member_email to null. Do NOT invent or guess email addresses.

Return ONLY a valid raw JSON object. Do not include markdown code block formatting or explanation.
"""

# In-Memory Cache for LLM responses: hash(model:cleaned_message) -> (timestamp, parsed_dict)
_PARSING_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def parse_request_with_llm(user_message: str) -> Dict[str, Any]:
    """
    Parses a user message using Gemini Flash (Vertex AI):
    1. In-Memory Cache Check -> Returns cached result if model + message key exists
    2. Gemini SDK Call -> Uses settings.gemini_model_name and handles region fallback if unavailable in asia-northeast1

    :param user_message: Natural language request message from Teams user
    :return: Dictionary containing parsed action, group_email, member_email
    """
    cleaned_message = _clean_teams_mention(user_message)
    model_name = settings.gemini_model_name
    logger.info(f"[AIパース開始] Gemini ({model_name}, リージョン: {settings.gcp_location}) で解析中: '{cleaned_message}'")

    # Key includes model_name so changing GEMINI_MODEL_NAME invalidates old cached parsing results
    cache_key = f"{model_name}:{cleaned_message}"
    msg_hash = hashlib.md5(cache_key.encode("utf-8")).hexdigest()

    # Cost-Optimization: Check In-Memory Parsing Cache
    if settings.llm_cost_enable_cache and msg_hash in _PARSING_CACHE:
        cache_time, cached_result = _PARSING_CACHE[msg_hash]
        if time.time() - cache_time < settings.llm_cost_cache_ttl_seconds:
            logger.info(f"[メモリキャッシュヒット] モデル '{model_name}' の過去の解析結果をキャッシュから即時取得しました (課金: 0)。")
            return cached_result.copy()

    # Call Google GenAI SDK (Vertex AI)
    try:
        from google import genai
        from google.genai import types

        project_id = settings.gcp_project_id.strip() if settings.gcp_project_id and settings.gcp_project_id.strip() else None
        
        # Primary regional client configuration (e.g. asia-northeast1)
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=settings.gcp_location,
            http_options=types.HttpOptions(timeout=3000)
        )

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=cleaned_message,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=settings.llm_cost_max_output_tokens
                )
            )
        except Exception as err:
            err_str = str(err).lower()
            # If specified model is unavailable in target region (e.g. 404 NOT_FOUND in asia-northeast1), attempt us-central1 fallback
            if "404" in err_str or "not found" in err_str:
                logger.warning(f"[リージョンモデル未対応] モデル '{model_name}' はリージョン '{settings.gcp_location}' で未提供です。us-central1 または GAモデルへフォールバックします ({err})...")
                fallback_client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location="us-central1",
                    http_options=types.HttpOptions(timeout=3000)
                )
                response = fallback_client.models.generate_content(
                    model=model_name,
                    contents=cleaned_message,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.0,
                        max_output_tokens=settings.llm_cost_max_output_tokens
                    )
                )
            else:
                raise err

        response_text = response.text.strip() if response.text else "{}"
        if response_text.startswith("```"):
            response_text = re.sub(r"^```(?:json)?\n?", "", response_text)
            response_text = re.sub(r"\n?```$", "", response_text).strip()

        parsed_result = json.loads(response_text)

        # Apply default group email fallback if group_email is missing
        if not parsed_result.get("group_email") and settings.default_group_email:
            parsed_result["group_email"] = settings.default_group_email

        # Save to In-Memory Cache
        if settings.llm_cost_enable_cache:
            _PARSING_CACHE[msg_hash] = (time.time(), parsed_result)

        logger.info(f"[AIパース成功] Google GenAI Vertex AI ({model_name}) による解析が完了しました: {parsed_result}")
        return parsed_result
    except Exception as e:
        logger.warning(
            f"[AIパース例外・タイムアウト] Google GenAI SDK の呼び出しに失敗しました (指定モデル: '{model_name}', エラー詳細: {e})。ヒューリスティックパーサーへフォールバックします。",
            exc_info=True
        )
        return _heuristic_fallback_parser(cleaned_message)


def _clean_teams_mention(text: str) -> str:
    """Removes HTML tags and Teams mention tags."""
    text = re.sub(r"<at>.*.*?/at>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _heuristic_fallback_parser(text: str) -> Dict[str, Any]:
    """
    Robust regex fallback parser when Gemini API is offline or in dev environment.
    """
    emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text)

    action = "add_member"
    if any(k in text for k in ["削除", "remove", "脱退", "delete"]):
        action = "remove_member"

    group_email = None
    member_email = None

    if len(emails) >= 2:
        group_candidate = [e for e in emails if any(k in e for k in ["group", "team", "ml", "dev", "pj"])]
        if group_candidate:
            group_email = group_candidate[0]
            member_email = [e for e in emails if e != group_email][0]
        else:
            group_email = emails[0]
            member_email = emails[1]
    elif len(emails) == 1:
        # If DEFAULT_GROUP_EMAIL is configured, assume single email in request is the target member
        if settings.default_group_email:
            member_email = emails[0]
            group_email = settings.default_group_email
        else:
            group_email = emails[0]

    if not group_email and settings.default_group_email:
        group_email = settings.default_group_email

    result = {
        "action": action,
        "group_email": group_email,
        "member_email": member_email
    }
    logger.info(f"[ヒューリスティックパース結果] 抽出結果: {result}")
    return result
