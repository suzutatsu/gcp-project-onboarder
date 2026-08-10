import asyncio
import logging
import time
from typing import Dict, Any
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, status
from app.config import settings
from app.security.hmac_verifier import verify_teams_signature
from app.security.guardrails import validate_request_guardrails, GuardrailValidationError
from app.security.token_service import token_service
from app.services.llm_parser import parse_request_with_llm
from app.services.workspace_service import workspace_group_service
from app.services.teams_notifier import (
    build_admin_approval_card_payload,
    send_admin_approval_card_async,
    send_teams_text_message_async
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("add-member-bot")

# In-Memory Webhook Deduplication Cache: (requester:text) -> timestamp
_PROCESSED_WEBHOOK_CACHE: Dict[str, float] = {}
DEDUPLICATION_TTL_SECONDS = 15.0

# FastAPI App initialization
app = FastAPI(
    title="GCP Project Onboarder",
    description="Stateless/DB-less Teams bot for managing Google Workspace Groups with human-in-the-loop approval.",
    version="1.0.0"
)


@app.get("/health")
def health_check():
    """Health check endpoint for Cloud Run container probes."""
    logger.info("[ヘルスチェック] リクエストを受信しました。サービスは正常に動作しています。")
    return {"status": "ok", "env": settings.env, "version": "1.0.0"}


@app.post("/webhook")
async def handle_teams_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Microsoft Teams Outgoing Webhook receiver (< 5s SLA).
    Handles single-channel (direct Adaptive Card response) and multi-channel (admin channel dispatch) modes cleanly.
    """
    body_bytes = await request.body()
    auth_header = request.headers.get("Authorization", "")

    # 1. HMAC Signature Verification
    if settings.teams_security_token:
        logger.info("[HMAC検証] 受信した Teams 署名の検証を開始します。")
        if not verify_teams_signature(body_bytes, auth_header, settings.teams_security_token):
            logger.warning("[HMAC検証失敗] Teams HMAC 署名が無効です。アクセスを拒否しました (401)。")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Teams HMAC Signature"
            )
        logger.info("[HMAC検証成功] 署名が正常に確認されました。")

    body_json = await request.json()
    user_text = body_json.get("text", "")
    requester = body_json.get("from", {}).get("name", "Teams User")

    logger.info(f"[WEBHOOK受信] 申請者 '{requester}' からメッセージを受信: '{user_text}'")

    # 2. Webhook Event Deduplication Check (Prevents duplicate messages & cards from Teams retries)
    now = time.time()
    dedup_key = f"{requester}:{user_text.strip()}"
    if dedup_key in _PROCESSED_WEBHOOK_CACHE:
        last_time = _PROCESSED_WEBHOOK_CACHE[dedup_key]
        if now - last_time < DEDUPLICATION_TTL_SECONDS:
            logger.info(f"[重複Webhook検知] 申請者 '{requester}' からの直前同文面リクエストの重複送信を検知しました。不可視レスポンス (Zero-Width Space) を返却します。")
            return {"type": "message", "text": "\u200b"}

    _PROCESSED_WEBHOOK_CACHE[dedup_key] = now

    payload = {
        "text": user_text,
        "requester": requester
    }

    # 3. Hybrid Response Logic: Single Channel vs Separate Admin Channel Mode
    if not settings.admin_webhook_url:
        # SINGLE CHANNEL / DEV MODE: Return Adaptive Card or prompt message directly in HTTP response (EXACTLY 1 POST)
        logger.info("[単一チャネルモード] ADMIN_WEBHOOK_URL 未設定のため、即時レスポンスとして直接 Adaptive Card または案内文面を返却します。")
        try:
            parsed_request = await asyncio.to_thread(parse_request_with_llm, user_text)
            parsed_request["requester"] = requester
            validate_request_guardrails(parsed_request)
            signed_token = token_service.create_approval_token(parsed_request)
            
            logger.info("[単一チャネルモード完了] 承認ボタン付き Adaptive Card を直接レスポンスとして 1 投稿のみ返却します。")
            return build_admin_approval_card_payload(parsed_request, signed_token)
        except GuardrailValidationError as e:
            logger.warning(f"[ガードレール判定案内] {e}")
            return {
                "type": "message",
                "text": f"ℹ️ **申請案内:** {e}"
            }
        except Exception as e:
            logger.error(f"[パース例外] リクエスト処理中にエラーが発生しました: {e}", exc_info=True)
            return {
                "type": "message",
                "text": f"❌ **システムエラー:** リクエストの処理中にエラーが発生しました: {e}"
            }
    else:
        # SEPARATE ADMIN CHANNEL MODE: Delegate card posting to background task and return instant receipt
        logger.info("[複数チャネルモード] ADMIN_WEBHOOK_URL 設定あり。バックグラウンドタスクへ管理者チャネル送信を委任します。")
        background_tasks.add_task(process_iam_request_async, payload)
        return {
            "type": "message",
            "text": "✅ **申請受付完了**\nご依頼を受理しました。管理者専用チャネルへ承認リクエストを送信しました。"
        }


@app.post("/approve")
async def handle_direct_approve(request: Request):
    """
    Endpoint called by Teams Adaptive Card submit action or approval button.
    Receives signed token, verifies HMAC signature, and executes Google Workspace API calls.
    """
    try:
        body = await request.json()
        token = body.get("token")
        approver = body.get("approver", "Teams Administrator")

        logger.info(f"[承認エンドポイント] 承認者 '{approver}' から承認処理リクエストを受信しました。")

        if not token:
            logger.warning("[承認エラー] リクエストボディにトークンが含まれていません。")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing token parameter")

        result = await execute_approval_action(token, approver)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[承認システムエラー] 承認処理中に例外が発生しました: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


async def process_iam_request_async(payload: Dict[str, Any]):
    """
    Background worker pipeline (used when ADMIN_WEBHOOK_URL is configured):
    1. Parse request with Gemini Flash Lite LLM
    2. Validate against Python safety guardrails
    3. Generate DB-less HMAC signed payload token
    4. Post Adaptive Card to Admin Channel (ADMIN_WEBHOOK_URL)
    """
    user_text = payload.get("text", "")
    requester = payload.get("requester", "Teams User")

    logger.info(f"[パイプライン開始] 申請者 '{requester}' のバックグラウンドタスク処理を開始します。")

    try:
        # Step 1: LLM Parsing
        logger.info("[ステップ 1/4: AIパース] Gemini Flash Lite で申請文面を自然言語解析中...")
        parsed_request = await asyncio.to_thread(parse_request_with_llm, user_text)
        parsed_request["requester"] = requester
        logger.info(f"[ステップ 1/4 完了] 解析結果: アクション='{parsed_request.get('action')}', 対象グループ='{parsed_request.get('group_email')}', 対象メンバー='{parsed_request.get('member_email')}'")

        # Step 2: Guardrail Validation
        logger.info("[ステップ 2/4: ガードレール] セキュリティ検証を実行中...")
        validate_request_guardrails(parsed_request)
        logger.info("[ステップ 2/4 完了] セキュリティガードレール検証に合格しました。")

        # Step 3: DB-less Signed Token Creation
        logger.info("[ステップ 3/4: トークン生成] DBレス HMAC-SHA256 署名付き承認トークンを生成中...")
        signed_token = token_service.create_approval_token(parsed_request)
        logger.info("[ステップ 3/4 完了] 署名トークンの生成が完了しました。")

        # Step 4: Send Approval Card to Admin Channel
        logger.info("[ステップ 4/4: 承認カード送信] 管理者専用チャネルへ承認ボタン付き Adaptive Card を自動投稿中...")
        sent = await send_admin_approval_card_async(
            webhook_url=settings.admin_webhook_url,
            request_data=parsed_request,
            signed_token=signed_token
        )

        if sent:
            logger.info(f"[パイプライン正常完了] 申請者 '{requester}' の承認カードを管理者チャネルへ送信完了しました。")
        else:
            logger.warning("[送信警告] 管理者チャネルへの承認カード投稿に失敗しました。")

    except GuardrailValidationError as e:
        error_msg = f"ℹ️ **申請案内 / セキュリティ判定:** {e}"
        logger.warning(f"[ガードレール判定結果] {e}")
        await send_teams_text_message_async(settings.notification_webhook_url or settings.admin_webhook_url, error_msg)
    except Exception as e:
        error_msg = f"❌ **リクエスト処理中にエラーが発生しました:** {e}"
        logger.error(f"[パイプラインエラー] 予期せぬシステム例外が発生しました: {e}", exc_info=True)
        await send_teams_text_message_async(settings.admin_webhook_url, error_msg, title="システムエラー")


async def execute_approval_action(token: str, approver: str = "Administrator") -> Dict[str, Any]:
    """
    Decodes and verifies token payload, then executes Workspace Group operations.
    Completion messages go to origin request channel (`NOTIFICATION_WEBHOOK_URL`),
    and admin logs go to `ADMIN_WEBHOOK_URL`.
    """
    logger.info("[実行ステップ 1/4: トークン検証] HMAC 署名および有効期限 (3日間) を検証中...")
    request_data = token_service.verify_signed_token(token)
    if not request_data:
        logger.warning("[承認実行失敗] 無効または有効期限切れの承認トークンです。")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="無効な承認トークンか、改ざん検出/有効期限切れです。"
        )
    logger.info(f"[トークン検証成功] トークンは正当です (申請ID: '{request_data.get('req_id')}')")

    # 2. Re-verify guardrails
    logger.info("[実行ステップ 2/4: ガードレール再確認] パラメータを安全ガードレールで再検証中...")
    try:
        validate_request_guardrails(request_data)
        logger.info("[ガードレール再確認成功] 安全性の再検証にパスしました。")
    except GuardrailValidationError as e:
        logger.warning(f"[ガードレール再確認失敗] ガードレール検証に失敗しました: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"ガードレール検証失敗: {e}")

    action = request_data.get("action")
    group_email = request_data.get("group_email")
    member_email = request_data.get("member_email")
    req_id = request_data.get("req_id", "REQ")

    api_response = {}
    success_message = ""

    try:
        # 3. Execute requested API action
        logger.info(f"[実行ステップ 3/4: API実行] Google Cloud Identity API を呼び出し中 (アクション: '{action}', 対象グループ: '{group_email}', 対象メンバー: '{member_email}')...")

        if action == "add_member":
            api_response = workspace_group_service.add_member_to_group(group_email, member_email)
            success_message = f"Googleグループ `{group_email}` にユーザー `{member_email}` を正常に追加しました。 (申請ID: {req_id})"

        elif action == "remove_member":
            api_response = workspace_group_service.remove_member_from_group(group_email, member_email)
            success_message = f"Googleグループ `{group_email}` からユーザー `{member_email}` を正常に削除しました。 (申請ID: {req_id})"

        else:
            raise ValueError(f"未知のアクションです: {action}")

        logger.info(f"[API実行成功] アクション '{action}' が完了しました: {success_message}")

        # 4. Completion notice goes to original request channel thread / notification channel
        logger.info("[実行ステップ 4/4: 通知送信] Teams チャネルへ処理完了通知を送信中...")
        completion_text = f"🎉 **申請の処理が完了しました**\n\n{success_message}\n(承認者: {approver})"
        await send_teams_text_message_async(settings.notification_webhook_url or settings.admin_webhook_url, completion_text)
        
        # Log to Admin Channel as well if configured
        if settings.admin_webhook_url:
            admin_log_text = f"ℹ️ **承認完了ログ** (ID: {req_id})\n{success_message} (承認者: {approver})"
            await send_teams_text_message_async(settings.admin_webhook_url, admin_log_text)

        logger.info(f"[承認ワークフロー完了] 申請ID '{req_id}' の一連の処理が正常に完結しました。")

        return {
            "status": "success",
            "message": success_message,
            "api_response": api_response
        }

    except Exception as e:
        error_msg = f"❌ **権限実行エラー (ID: {req_id}):** {e}"
        logger.error(f"[API実行エラー] Google Workspace 操作の実行に失敗しました: {e}", exc_info=True)
        if settings.admin_webhook_url:
            await send_teams_text_message_async(settings.admin_webhook_url, error_msg, title="⚠️ Google Workspace 処理エラー")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"権限実行エラー: {str(e)}"
        )
