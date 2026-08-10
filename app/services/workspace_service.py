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
            credentials, _ = google.auth.default(scopes=SCOPES)
            self._service = build("cloudidentity", "v1", credentials=credentials, cache_discovery=False)
        return self._service

    def get_group_id_by_email(self, group_email: str) -> str:
        """
        Looks up Cloud Identity Group resource name (`groups/{group_id}`) from its email address.
        """
        service = self._get_service()
        response = service.groups().lookup(groupEmail=group_email).execute(num_retries=3)
        group_name = response.get("name")  # Format: groups/{group_id}
        if not group_name:
            raise ValueError(f"指定されたGoogleグループが見つかりません: {group_email}")
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
            logger.info(f"Adding '{member_email}' to group '{group_email}' ({group_name})...")
            request = service.groups().memberships().create(parent=group_name, body=body)
            response = request.execute(num_retries=3)
            logger.info(f"Successfully added '{member_email}' to group '{group_email}'. Response: {response}")
            return response
        except HttpError as e:
            if e.resp.status == 409:
                logger.info(f"User '{member_email}' is already a member of group '{group_email}'.")
                return {"status": "ALREADY_EXISTS", "message": f"{member_email} は既にグループのメンバーです。"}
            logger.error(f"Failed to add member to group: {e}", exc_info=True)
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
            # List memberships to locate user's membership resource name
            memberships = service.groups().memberships().list(parent=group_name).execute(num_retries=3)
            membership_name = None

            for member in memberships.get("memberships", []):
                member_key = member.get("preferredMemberKey", {}).get("id", "")
                if member_key.lower() == member_email.lower():
                    membership_name = member.get("name")
                    break

            if not membership_name:
                logger.info(f"User '{member_email}' was not found in group '{group_email}'.")
                return {"status": "NOT_FOUND", "message": f"{member_email} はグループに存在しません。"}

            # Delete membership
            logger.info(f"Deleting membership '{membership_name}' for '{member_email}'...")
            delete_req = service.groups().memberships().delete(name=membership_name)
            response = delete_req.execute(num_retries=3)
            logger.info(f"Successfully removed '{member_email}' from group '{group_email}'.")
            return response
        except HttpError as e:
            logger.error(f"Failed to remove member from group: {e}", exc_info=True)
            raise


# Global singleton instance
workspace_group_service = WorkspaceGroupService()
