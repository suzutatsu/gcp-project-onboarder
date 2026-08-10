import logging
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Application Settings loaded from environment variables or .env file.
    Designed for zero secret storage on disk (Keyless / Ephemeral In-Memory Keys).
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application environment
    env: str = Field(default="development", description="Environment mode: development | production")

    # Microsoft Teams Security Configuration
    teams_security_token: str = Field(
        default="",
        alias="TEAMS_SECURITY_TOKEN",
        description="Base64 encoded security token from Teams Outgoing Webhook"
    )
    admin_webhook_url: str = Field(
        default="",
        alias="ADMIN_WEBHOOK_URL",
        description="Teams Admin channel incoming webhook URL for approval requests"
    )
    notification_webhook_url: str = Field(
        default="",
        alias="NOTIFICATION_WEBHOOK_URL",
        description="Teams origin channel incoming webhook URL for status notifications"
    )

    # GCP Configuration
    gcp_project_id: str = Field(
        default="",
        alias="GCP_PROJECT_ID",
        description="Google Cloud Project ID (auto-detected via GCP metadata if blank)"
    )
    gcp_location: str = Field(
        default="us",
        alias="GCP_LOCATION",
        description="Google Cloud location region (default 'us' for US Multi-Region endpoint)"
    )
    gemini_model_name: str = Field(
        default="gemini-3.5-flash-lite",
        alias="GEMINI_MODEL_NAME",
        description="Ultra-fast & ultra-low cost Gemini model (default gemini-3.5-flash-lite)"
    )

    # LLM Cost Optimization Configuration
    llm_cost_enable_cache: bool = Field(
        default=True,
        alias="LLM_COST_ENABLE_CACHE",
        description="Enable in-memory response caching for Gemini LLM calls to reduce duplicate token billing"
    )
    llm_cost_cache_ttl_seconds: int = Field(
        default=2592000,
        alias="LLM_COST_CACHE_TTL_SECONDS",
        description="TTL in seconds for Gemini parsing result cache (default 30 days)"
    )
    llm_cost_max_output_tokens: int = Field(
        default=150,
        alias="LLM_COST_MAX_OUTPUT_TOKENS",
        description="Cap Gemini output token generation to minimize API billing per request"
    )

    # DB-less HMAC Token Signing Configuration (Zero secret storage on disk)
    secret_key: str = Field(
        default="",
        alias="SECRET_KEY",
        description="Secret key for signing tokens. If blank, an ephemeral key is generated in RAM at startup."
    )
    token_ttl_seconds: int = Field(
        default=259200,  # 3 days default
        alias="TOKEN_TTL_SECONDS",
        description="Token expiration TTL in seconds (default 3 days / 72 hours)"
    )

    # Default Target Google Group Email (Optional fixed fallback)
    default_group_email: str = Field(
        default="",
        alias="DEFAULT_GROUP_EMAIL",
        description="Default target Google Group email if omitted in user message"
    )

    # Security Guardrails / Whitelist (Configurable & Injectable externally)
    allowed_domains_raw: str = Field(
        default="",
        alias="ALLOWED_EMAIL_DOMAINS",
        description="Comma-separated allowed member email domains e.g. 'company.com,subsidiary.com' (Injectable via ENV)"
    )

    @property
    def allowed_domains(self) -> List[str]:
        return [d.strip().lower() for d in self.allowed_domains_raw.split(",") if d.strip()]

    def check_security_warnings(self):
        """Logs security diagnostics."""
        if not self.secret_key:
            logger.info("[セキュリティ] メモリ内一次性署名鍵 (RAM Key) を生成しました (ディスク保存ゼロ)。")

        if self.allowed_domains:
            logger.info(f"[ガードレール] 許可ドメインリストを設定しました: {self.allowed_domains}")


# Global settings singleton instance
settings = Settings()
settings.check_security_warnings()
