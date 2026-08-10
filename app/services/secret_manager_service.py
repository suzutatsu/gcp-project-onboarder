import logging
from typing import Optional
import google.auth

logger = logging.getLogger(__name__)


def fetch_secret_from_manager(secret_id: str, project_id: str, version: str = "latest") -> Optional[str]:
    """
    Fetches a secret payload from Google Secret Manager dynamically at runtime.
    No secrets are written to disk; payload is returned directly in memory.

    :param secret_id: Secret Manager secret name (e.g. 'teams-security-token')
    :param project_id: GCP project ID
    :param version: Secret version (default 'latest')
    :return: Secret string if fetched successfully, None otherwise
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"

        logger.info(f"Fetching secret '{secret_id}' from Secret Manager...")
        response = client.access_secret_version(request={"name": name})
        secret_value = response.payload.data.decode("utf-8")
        return secret_value
    except Exception as e:
        logger.warning(f"Could not fetch secret '{secret_id}' from Secret Manager ({e}).")
        return None
