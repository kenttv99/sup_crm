from __future__ import annotations

import asyncio
import importlib
import inspect
import os
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


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings() -> tuple[Any | None, str | None]:
    add_project_root()
    candidates = (
        "settings",
        "config.settings",
        "config",
        "app.settings",
        "app.config",
        "bot.settings",
        "bot.config",
        "database.config",
    )
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue

        for attr in ("settings", "config", "SETTINGS"):
            if hasattr(module, attr):
                value = getattr(module, attr)
                if inspect.ismodule(value):
                    continue
                return value, f"{module_name}.{attr}"

        get_settings = getattr(module, "get_settings", None)
        if callable(get_settings):
            try:
                return get_settings(), f"{module_name}.get_settings()"
            except Exception:
                continue

        for attr in ("Settings", "Config"):
            cls = getattr(module, attr, None)
            if inspect.isclass(cls):
                try:
                    return cls(), f"{module_name}.{attr}()"
                except Exception:
                    continue

    return None, None


def settings_to_dict(settings: Any | None) -> dict[str, Any]:
    if settings is None:
        return {}
    if isinstance(settings, dict):
        return dict(settings)
    if hasattr(settings, "model_dump"):
        return dict(settings.model_dump())
    if hasattr(settings, "dict"):
        return dict(settings.dict())

    result: dict[str, Any] = {}
    for name in dir(settings):
        if name.startswith("_"):
            continue
        try:
            value = getattr(settings, name)
        except Exception:
            continue
        if inspect.ismethod(value) or inspect.isfunction(value):
            continue
        if isinstance(value, (str, int, float, bool, type(None))):
            result[name] = value
    return result


def env_or_setting(names: tuple[str, ...], settings: dict[str, Any] | None = None) -> Any | None:
    settings = settings or {}
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value

    lower_settings = {key.lower(): value for key, value in settings.items()}
    for name in names:
        value = settings.get(name)
        if value not in (None, ""):
            return value
        value = lower_settings.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def mask_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if "url" in key.lower() or "dsn" in key.lower():
        try:
            parts = urlsplit(text)
            if parts.password:
                host = parts.hostname or ""
                if parts.port:
                    host = f"{host}:{parts.port}"
                user = parts.username or ""
                netloc = f"{user}:***@{host}" if user else host
                return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        except Exception:
            return "***"
    secret_words = ("token", "secret", "password", "passwd", "pwd", "key")
    if any(word in key.lower() for word in secret_words):
        if not text:
            return ""
        return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "***"
    return value


def database_url(settings: dict[str, Any] | None = None) -> str | None:
    url = env_or_setting(
        (
            "DATABASE_URL",
            "POSTGRES_DSN",
            "POSTGRES_URL",
            "DB_URL",
            "SQLALCHEMY_DATABASE_URL",
        ),
        settings,
    )
    if url:
        return str(url)

    host = env_or_setting(("DB_HOST", "POSTGRES_HOST"), settings)
    name = env_or_setting(("DB_NAME", "POSTGRES_DB", "POSTGRES_DATABASE"), settings)
    user = env_or_setting(("DB_USER", "POSTGRES_USER"), settings)
    password = env_or_setting(("DB_PASSWORD", "POSTGRES_PASSWORD"), settings)
    port = env_or_setting(("DB_PORT", "POSTGRES_PORT"), settings) or "5432"
    if host and name and user:
        auth = str(user)
        if password:
            auth += f":{password}"
        return f"postgresql+psycopg://{auth}@{host}:{port}/{name}"
    return None


def async_database_url(settings: dict[str, Any] | None = None) -> str | None:
    url = database_url(settings)
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def parse_int(value: Any, name: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be an integer, got {value!r}") from exc
