from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    support_chat_id: int = Field(alias="SUPPORT_CHAT_ID")
    database_url: str = Field(alias="DATABASE_URL")
    sync_database_url: str = Field(alias="SYNC_DATABASE_URL")
    async_database_url: str = Field(alias="ASYNC_DATABASE_URL")
    sql_echo: bool = Field(alias="SQL_ECHO")
    redis_url: str = Field(alias="REDIS_URL")
    webhook_base_url: str = Field(alias="WEBHOOK_BASE_URL")
    webhook_path: str = Field(alias="WEBHOOK_PATH")
    webhook_secret_token: str = Field(alias="WEBHOOK_SECRET_TOKEN")
    drop_pending_updates: bool = Field(alias="DROP_PENDING_UPDATES")
    admin_ids_csv: str = Field(alias="ADMIN_IDS")
    app_host: str = Field(alias="APP_HOST")
    app_port: int = Field(alias="APP_PORT")
    log_level: str = Field(alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        populate_by_name=True,
    )

    @field_validator("webhook_base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("webhook_path")
    @classmethod
    def normalize_webhook_path(cls, value: str) -> str:
        path = value if value.startswith("/") else f"/{value}"
        if len(path) > 1:
            return path.rstrip("/")
        return path

    @field_validator(
        "bot_token",
        "database_url",
        "sync_database_url",
        "async_database_url",
        "redis_url",
        "webhook_base_url",
        "webhook_path",
        "webhook_secret_token",
        "app_host",
        "log_level",
    )
    @classmethod
    def reject_empty_strings(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be set in .env")
        return value

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url}{self.webhook_path}"

    @property
    def admin_ids(self) -> frozenset[int]:
        if not self.admin_ids_csv:
            return frozenset()
        return frozenset(
            int(item.strip())
            for item in self.admin_ids_csv.split(",")
            if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
