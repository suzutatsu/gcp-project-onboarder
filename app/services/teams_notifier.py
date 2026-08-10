import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def build_admin_approval_card_payload(request_data: Dict[str, Any], signed_token: str) -> Dict[str, Any]:
    """
    Constructs the Teams Adaptive Card payload JSON structure for human approval.
    """
    action_label = {
        "add_member": "➕ グループメンバー追加",
        "remove_member": "➖ グループメンバー削除"
    }.get(request_data.get("action"), request_data.get("action"))

    req_id = request_data.get("req_id", "REQ-NEW")

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "Large",
                            "weight": "Bolder",
                            "color": "Attention",
                            "text": "🛡️ Google Workspace グループ管理 承認リクエスト"
                        },
                        {
                            "type": "TextBlock",
                            "text": f"申請ID: **{req_id}** の承認リクエストが届きました。内容を確認し、承認操作を行ってください。",
                            "wrap": True,
                            "isSubtle": True
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "申請種別:", "value": str(action_label)},
                                {"title": "対象グループ:", "value": str(request_data.get("group_email") or "-")},
                                {"title": "対象メンバー:", "value": str(request_data.get("member_email") or "-")},
                                {"title": "申請者:", "value": str(request_data.get("requester") or "Teamsユーザー")}
                            ]
                        },
                        {
                            "type": "TextBlock",
                            "text": f"💬 **手動コマンド承認の場合:**\n`@GCP Onboarder 承認 token:{signed_token}`",
                            "wrap": True,
                            "size": "Small",
                            "isSubtle": True
                        }
                    ],
                    "actions": [
                        {
                            "type": "Action.Submit",
                            "title": "✅ 承認して自動実行",
                            "data": {
                                "action": "approve",
                                "token": signed_token
                            }
                        }
                    ]
                }
            }
        ]
    }


async def send_admin_approval_card_async(webhook_url: str, request_data: Dict[str, Any], signed_token: str) -> bool:
    """
    Asynchronously sends an Adaptive Card to Teams Admin Channel for approval.

    :param webhook_url: Teams Incoming Webhook URL for admin channel
    :param request_data: Parsed request data dict
    :param signed_token: DB-less HMAC signed payload token
    :return: True if successfully sent, False otherwise
    """
    if not webhook_url:
        logger.warning("[通知警告] ADMIN_WEBHOOK_URL が設定されていないため、管理者チャネルへのカード送信をスキップします。")
        return False

    card_payload = build_admin_approval_card_payload(request_data, signed_token)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=card_payload)
            response.raise_for_status()
            logger.info(f"[カード送信成功] 管理者 Webhook へ承認 Adaptive Card を送信しました ({response.status_code})。")
            return True
    except Exception as e:
        logger.error(f"[カード送信エラー] 管理者 Webhook へのカード送信に失敗しました: {e}", exc_info=True)
        return False


async def send_teams_text_message_async(webhook_url: str, message: str, title: Optional[str] = None) -> bool:
    """
    Asynchronously sends a notification message to a Teams Webhook.
    """
    if not webhook_url:
        logger.warning("[通知警告] Webhook URL が設定されていないため、テキスト通知送信をスキップします。")
        return False

    payload = {"text": f"**{title}**\n\n{message}" if title else message}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
            logger.info("[テキスト送信成功] Teams へテキストメッセージを正常に送信しました。")
            return True
    except Exception as e:
        logger.error(f"[テキスト送信エラー] Teams テキストメッセージの送信に失敗しました: {e}", exc_info=True)
        return False
