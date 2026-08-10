import logging
from typing import Dict, Any, Optional
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/cloud-identity.groups"
]


class WorkspaceGroupService:
    """
    Manages Google Workspace / Cloud Identity Group memberships.
    Operates using Service Account Manager permissions without needing Workspace Super Admin role.
    """

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service is None:
            logger.info("[WORKSPACE 認証] Application Default Credentials (ADC) で Google API クライアントを認証中...")
            credentials, _ = google.auth.default(scopes=SCOPES)
            self._service = build("cloudidentity", "v1", credentials=credentials, cache_discovery=False)
            logger.info("[WORKSPACE 認証成功] Cloud Identity API サービスが正常に構築されました。")
        return self._service

    def get_group_id_by_email(self, group_email: str) -> str:
        """
        Looks up Cloud Identity Group resource name (`groups/{group_id}`) from its email address.
        """
        logger.info(f"[グループ検索] メールアドレス '{group_email}' からグループIDを検索中...")
        service = self._get_service()
        response = service.groups().lookup(groupEmail=group_email).execute(num_retries=3)
        group_name = response.get("name")  # Format: groups/{group_id}
        if not group_name:
            logger.error(f"[グループ検索エラー] 対象の Google グループが見つかりません: '{group_email}'")
            raise ValueError(f"指定されたGoogleグループが見つかりません: {group_email}")
        logger.info(f"[グループ検索完了] リソース名を取得しました: '{group_name}' (グループ: '{group_email}')")
        return group_name

    def add_member_to_group(self, group_email: str, member_email: str, role: str = "MEMBER") -> Dict[str, Any]:
        """
        Adds a user to a Google Group using Cloud Identity Groups API.

        :param group_email: Email address of target Google Group
        :param member_email: Email address of user to add
        :param role: Member role in group ("MEMBER" or "MANAGER")
        :return: API response dictionary
        """
        service = self._get_service()
        group_name = self.get_group_id_by_email(group_email)

        body = {
            "preferredMemberKey": {"id": member_email},
            "roles": [{"name": role}]
        }

        try:
            logger.info(f"[メンバー追加開始] グループ '{group_email}' に '{member_email}' を追加中 (役割: {role})...")
            request = service.groups().memberships().create(parent=group_name, body=body)
            response = request.execute(num_retries=3)
            logger.info(f"[メンバー追加成功] グループ '{group_email}' に '{member_email}' を正常に追加しました。リソース名: '{response.get('name')}'")
            return response
        except HttpError as e:
            if e.resp.status == 409:
                logger.info(f"[既にメンバー] ユーザー '{member_email}' は既にグループ '{group_email}' のメンバーです。")
                return {"status": "ALREADY_EXISTS", "message": f"{member_email} は既にグループのメンバーです。"}
            logger.error(f"[メンバー追加エラー] グループ '{group_email}' へのメンバー追加に失敗しました: {e}", exc_info=True)
            raise

    def remove_member_from_group(self, group_email: str, member_email: str) -> Dict[str, Any]:
        """
        Removes a user from a Google Group using Cloud Identity Groups API.

        :param group_email: Email address of target Google Group
        :param member_email: Email address of user to remove
        :return: API response dictionary
        """
        service = self._get_service()
        group_name = self.get_group_id_by_email(group_email)

        try:
            logger.info(f"[メンバー削除開始] グループ '{group_email}' 内の '{member_email}' のメンバーシップ情報を検索中...")
            # List memberships to locate user's membership resource name
            memberships = service.groups().memberships().list(parent=group_name).execute(num_retries=3)
            membership_name = None

            for member in memberships.get("memberships", []):
                member_key = member.get("preferredMemberKey", {}).get("id", "")
                if member_key.lower() == member_email.lower():
                    membership_name = member.get("name")
                    break

            if not membership_name:
                logger.info(f"[グループ内に不在] ユーザー '{member_email}' はグループ '{group_email}' に存在しません。")
                return {"status": "NOT_FOUND", "message": f"{member_email} はグループに存在しません。"}

            # Delete membership
            logger.info(f"[メンバーシップ削除] メンバーシップ '{membership_name}' ('{member_email}') を削除中...")
            delete_req = service.groups().memberships().delete(name=membership_name)
            response = delete_req.execute(num_retries=3)
            logger.info(f"[メンバー削除成功] グループ '{group_email}' から '{member_email}' を正常に削除しました。")
            return response
        except HttpError as e:
            logger.error(f"[メンバー削除エラー] グループ '{group_email}' からのメンバー削除に失敗しました: {e}", exc_info=True)
            raise


# Global singleton instance
workspace_group_service = WorkspaceGroupService()
