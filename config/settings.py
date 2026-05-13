from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    support_chat_id: int = Field(alias="SUPPORT_CHAT_ID")
    webhook_base_url: str = Field(alias="WEBHOOK_BASE_URL")
    webhook_path: str = Field("/webhook", alias="WEBHOOK_PATH")
    webhook_secret_token: str = Field(alias="WEBHOOK_SECRET_TOKEN")
    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")
    database_url: str = Field(alias="DATABASE_URL")
    sql_echo: bool = Field(False, alias="SQL_ECHO")
    drop_pending_updates: bool = Field(False, alias="DROP_PENDING_UPDATES")
    admin_ids_csv: str = Field("", alias="ADMIN_IDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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
