from __future__ import annotations

import importlib
from typing import Any

from _common import load_settings


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
    raise RuntimeError("Webhook route is not registered")


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
    settings = load_settings()

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

    right = client.post(path, json=payload, headers={SECRET_HEADER: settings.webhook_secret_token})
    print(f"right_secret_status: {right.status_code}")
    return 0 if wrong.status_code == 401 and right.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
