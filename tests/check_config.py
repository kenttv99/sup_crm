from __future__ import annotations

import json
import os

from _common import (
    async_database_url,
    env_or_setting,
    load_dotenv,
    load_settings,
    mask_value,
    parse_int,
    settings_to_dict,
)


REQUIRED_GROUPS = {
    "BOT_TOKEN": ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    "SUPPORT_CHAT_ID": ("SUPPORT_CHAT_ID", "SUPPORT_GROUP_ID", "SUPPORT_CHAT"),
    "DATABASE_URL": (
        "DATABASE_URL",
        "POSTGRES_DSN",
        "POSTGRES_URL",
        "DB_URL",
        "SQLALCHEMY_DATABASE_URL",
    ),
}


def main() -> int:
    load_dotenv()
    settings_obj, source = load_settings()
    settings = settings_to_dict(settings_obj)

    visible = dict(settings)
    for key, value in os.environ.items():
        if key.startswith(("BOT_", "TELEGRAM_", "SUPPORT_", "WEBHOOK_", "DATABASE_", "POSTGRES_", "DB_")):
            visible.setdefault(key, value)

    sanitized = {key: mask_value(key, value) for key, value in sorted(visible.items())}
    print(f"settings_source: {source or 'not found; using environment only'}")
    print(json.dumps(sanitized, indent=2, ensure_ascii=False, default=str))

    missing = []
    invalid = []
    for logical_name, names in REQUIRED_GROUPS.items():
        if logical_name == "DATABASE_URL":
            if not async_database_url(settings):
                missing.append("one of DATABASE_URL/POSTGRES_DSN/POSTGRES_URL/DB_URL or DB_HOST+DB_NAME+DB_USER")
            continue
        if env_or_setting(names, settings) in (None, ""):
            missing.append(" or ".join(names))

    support_chat_id = env_or_setting(REQUIRED_GROUPS["SUPPORT_CHAT_ID"], settings)
    if support_chat_id not in (None, ""):
        try:
            parse_int(support_chat_id, "SUPPORT_CHAT_ID")
        except SystemExit as exc:
            invalid.append(str(exc))

    if missing:
        print("missing_required:")
        for item in missing:
            print(f"- {item}")
    if invalid:
        print("invalid_config:")
        for item in invalid:
            print(f"- {item}")
    if missing or invalid:
        return 1

    print("required_config: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
