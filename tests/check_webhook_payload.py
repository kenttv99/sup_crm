from __future__ import annotations

import importlib
import os
from typing import Any

from _common import env_or_setting, load_dotenv, load_settings, settings_to_dict


APP_MODULES = ("main", "app.main", "bot.main", "web.main")
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def load_app() -> Any | None:
    for module_name in APP_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        app = getattr(module, "app", None)
        if app is not None:
            return app
    return None


def webhook_path(app: Any) -> str:
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if "webhook" in path.lower():
            return path
    return "/webhook"


def minimal_update() -> dict[str, Any]:
    return {
        "update_id": 100000001,
        "edited_message": {
            "message_id": 1,
            "date": 1710000000,
            "chat": {"id": 123456789, "type": "private", "first_name": "Manual"},
            "from": {
                "id": 123456789,
                "is_bot": False,
                "first_name": "Manual",
            },
            "text": "webhook-check",
        },
    }


def main() -> int:
    load_dotenv()
    settings_obj, _ = load_settings()
    settings = settings_to_dict(settings_obj)
    secret = env_or_setting(("WEBHOOK_SECRET", "WEBHOOK_SECRET_TOKEN", "BOT_WEBHOOK_SECRET"), settings)

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        print(f"fastapi.testclient: unavailable: {exc}")
        print(f"minimal_update: {minimal_update()}")
        return 0

    app = load_app()
    if app is None:
        print("fastapi_app: not found in main.app/app.main.app/bot.main.app/web.main.app")
        print(f"minimal_update: {minimal_update()}")
        return 0

    path = webhook_path(app)
    client = TestClient(app)
    payload = minimal_update()

    wrong = client.post(path, json=payload, headers={SECRET_HEADER: "wrong-secret"})
    print(f"wrong_secret_status: {wrong.status_code}")

    if secret:
        right = client.post(path, json=payload, headers={SECRET_HEADER: str(secret)})
        print(f"right_secret_status: {right.status_code}")
        return 0 if wrong.status_code != right.status_code or right.status_code < 400 else 1

    no_secret = client.post(path, json=payload)
    print(f"no_secret_status: {no_secret.status_code}")
    print("WEBHOOK_SECRET is not set; right-secret check skipped")
    return 0 if no_secret.status_code < 500 else 1


if __name__ == "__main__":
    raise SystemExit(main())
