import asyncio
import logging
from typing import Dict, Any
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, status
from app.config import settings
from app.security.hmac_verifier import verify_teams_signature
from app.security.guardrails import validate_request_guardrails, GuardrailValidationError
from app.security.token_service import token_service
from app.services.llm_parser import parse_request_with_llm
from app.services.workspace_service import workspace_group_service
from app.services.teams_notifier import (
    send_admin_approval_card_async,
    send_teams_text_message_async
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("add-member-bot")

# FastAPI App initialization
app = FastAPI(
    title="GCP Project Onboarder",
    description="Stateless/DB-less Teams bot for managing Google Workspace Groups and Google Cloud IAM roles with human-in-the-loop approval.",
    version="1.0.0"
)


@app.get("/health")
def health_check():
    """Health check endpoint for Cloud Run container probes."""
    return {"status": "ok", "env": settings.env, "version": "1.0.0"}


@app.post("/webhook")
async def handle_teams_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Microsoft Teams Outgoing Webhook receiver (< 5s SLA).
    Verifies HMAC-SHA256 signature, responds instantly (< 50ms), and delegates LLM parsing
    and approval card dispatch to FastAPI BackgroundTasks.
    """
    body_bytes = await request.body()
    auth_header = request.headers.get("Authorization", "")

    # 1. HMAC Signature Verification
    if settings.teams_security_token:
        if not verify_teams_signature(body_bytes, auth_header, settings.teams_security_token):
            logger.warning("🚨 Invalid Teams HMAC signature. Access denied.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Teams HMAC Signature"
            )

    body_json = await request.json()
    user_text = body_json.get("text", "")
    requester = body_json.get("from", {}).get("name", "Teams User")

    payload = {
        "text": user_text,
        "requester": requester
    }

    # 2. Enqueue background task for LLM parsing & Admin card posting
    background_tasks.add_task(process_iam_request_async, payload)

    # 3. Respond immediately to Teams (< 50ms response to guarantee < 5s SLA)
    return {
        "type": "message",
        "text": "✅ **申請受付完了**\nご依頼を受理しました。AIがメッセージ内容を解析し、管理者の承認手続きへ進行します。"
    }


@app.post("/approve")
async def handle_direct_approve(request: Request):
    """
    Endpoint called by Teams Adaptive Card submit action or approval button.
    Receives signed token, verifies HMAC signature, and executes Google Cloud/Workspace API calls.
    """
    try:
        body = await request.json()
        token = body.get("token")
        approver = body.get("approver", "Teams Administrator")

        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing token parameter")

        result = await execute_approval_action(token, approver)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling approve endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


async def process_iam_request_async(payload: Dict[str, Any]):
    """
    Background worker pipeline:
    1. Parse request with Gemini Flash Lite LLM
    2. Validate against Python safety guardrails
    3. Generate DB-less HMAC signed payload token
    4. Post Adaptive Card to Admin Channel (ADMIN_WEBHOOK_URL)
    """
    user_text = payload.get("text", "")
    requester = payload.get("requester", "Teams User")

    try:
        # Step 1: LLM Parsing
        parsed_request = await asyncio.to_thread(parse_request_with_llm, user_text)
        parsed_request["requester"] = requester

        # Step 2: Guardrail Validation
        validate_request_guardrails(parsed_request)

        # Step 3: DB-less Signed Token Creation
        signed_token = token_service.create_approval_token(parsed_request)

        # Step 4: Send Approval Card to Admin Channel
        sent = await send_admin_approval_card_async(
            webhook_url=settings.admin_webhook_url,
            request_data=parsed_request,
            signed_token=signed_token
        )

        if not sent:
            logger.warning("Failed to send approval card to Admin channel via Webhook.")

    except GuardrailValidationError as e:
        # User-facing prompt or security rejection notice
        error_msg = f"ℹ️ **申請案内 / セキュリティ判定:** {e}"
        logger.warning(error_msg)
        await send_teams_text_message_async(settings.notification_webhook_url or settings.admin_webhook_url, error_msg)
    except Exception as e:
        error_msg = f"❌ **リクエスト処理中にエラーが発生しました:** {e}"
        logger.error(error_msg, exc_info=True)
        await send_teams_text_message_async(settings.admin_webhook_url, error_msg, title="システムエラー")


async def execute_approval_action(token: str, approver: str = "Administrator") -> Dict[str, Any]:
    """
    Decodes and verifies token payload, then executes Workspace Group or Google Cloud IAM operations.
    Completion messages go to the origin request channel/thread (`NOTIFICATION_WEBHOOK_URL`),
    and admin logs go to `ADMIN_WEBHOOK_URL`.
    """
    # 1. Verify token signature and expiration
    request_data = token_service.verify_signed_token(token)
    if not request_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="無効な承認トークンか、改ざん検出/有効期限切れです。"
        )

    # 2. Re-verify guardrails
    try:
        validate_request_guardrails(request_data)
    except GuardrailValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"ガードレール検証失敗: {e}")

    action = request_data.get("action")
    group_email = request_data.get("group_email")
    member_email = request_data.get("member_email")
    role = request_data.get("role")
    project_id = request_data.get("project_id", settings.gcp_project_id)
    req_id = request_data.get("req_id", "REQ")

    api_response = {}
    success_message = ""

    try:
        # 3. Execute requested API action
        if action == "add_member":
            api_response = workspace_group_service.add_member_to_group(group_email, member_email)
            success_message = f"Googleグループ `{group_email}` にユーザー `{member_email}` を正常に追加しました。 (申請ID: {req_id})"

        elif action == "remove_member":
            api_response = workspace_group_service.remove_member_from_group(group_email, member_email)
            success_message = f"Googleグループ `{group_email}` からユーザー `{member_email}` を正常に削除しました。 (申請ID: {req_id})"

        else:
            raise ValueError(f"未知のアクションです: {action}")

        # 4. Completion notice goes to original request channel thread / notification channel
        completion_text = f"🎉 **申請の処理が完了しました**\n\n{success_message}\n(承認者: {approver})"
        await send_teams_text_message_async(settings.notification_webhook_url or settings.admin_webhook_url, completion_text)
        
        # Log to Admin Channel as well
        admin_log_text = f"ℹ️ **承認完了ログ** (ID: {req_id})\n{success_message} (承認者: {approver})"
        await send_teams_text_message_async(settings.admin_webhook_url, admin_log_text)

        return {
            "status": "success",
            "message": success_message,
            "api_response": api_response
        }

    except Exception as e:
        error_msg = f"❌ **権限実行エラー (ID: {req_id}):** {e}"
        logger.error(error_msg, exc_info=True)
        # Errors go to Admin Channel
        await send_teams_text_message_async(settings.admin_webhook_url, error_msg, title="⚠️ Google Cloud / Workspace IAM 処理エラー")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"権限実行エラー: {str(e)}"
        )
