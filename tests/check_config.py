from __future__ import annotations

import json

from _common import (
    load_settings,
    mask_value,
    settings_to_dict,
)


def main() -> int:
    settings_obj = load_settings()
    settings = settings_to_dict(settings_obj)

    sanitized = {key: mask_value(key, value) for key, value in sorted(settings.items())}
    print("settings_source: config.settings.get_settings()")
    print(json.dumps(sanitized, indent=2, ensure_ascii=False, default=str))

    invalid = []
    if "replace_with" in settings_obj.bot_token:
        invalid.append("BOT_TOKEN still contains placeholder text")
    if settings_obj.support_chat_id == 0:
        invalid.append("SUPPORT_CHAT_ID must be the real Telegram support chat id, not 0")

    if invalid:
        print("invalid_config:")
        for item in invalid:
            print(f"- {item}")
        return 1

    print("required_config: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
