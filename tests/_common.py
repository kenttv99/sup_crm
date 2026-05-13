from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]


def configure_windows_event_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


configure_windows_event_loop_policy()


def add_project_root() -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def load_settings() -> Any:
    configure_windows_event_loop_policy()
    add_project_root()

    from config.settings import get_settings

    return get_settings()


def settings_to_dict(settings: Any) -> dict[str, Any]:
    return dict(settings.model_dump())


def mask_value(key: str, value: Any) -> Any:
    if value is None:
        return None

    text = str(value)
    if "url" in key.lower() or "dsn" in key.lower():
        parts = urlsplit(text)
        if parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            user = parts.username or ""
            netloc = f"{user}:***@{host}" if user else host
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    secret_words = ("token", "secret", "password", "passwd", "pwd", "key")
    if any(word in key.lower() for word in secret_words):
        return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "***"
    return value


def parse_int(value: Any, name: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be an integer, got {value!r}") from exc
