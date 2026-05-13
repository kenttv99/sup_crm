from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot

from _common import env_or_setting, load_dotenv, load_settings, parse_int, settings_to_dict


async def check() -> int:
    load_dotenv()
    settings_obj, _ = load_settings()
    settings = settings_to_dict(settings_obj)

    token = env_or_setting(("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"), settings)
    chat_id_raw = env_or_setting(("SUPPORT_CHAT_ID", "SUPPORT_GROUP_ID", "SUPPORT_CHAT"), settings)
    if not token or not chat_id_raw:
        print("BOT_TOKEN and SUPPORT_CHAT_ID are required")
        return 1

    chat_id = parse_int(chat_id_raw, "SUPPORT_CHAT_ID")
    if "replace_with" in str(token) or chat_id == 0:
        print("Replace BOT_TOKEN and SUPPORT_CHAT_ID in .env before calling Telegram.")
        return 1

    title = f"manual-check-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    bot = Bot(token=str(token))
    try:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=title)
        print(f"title: {title}")
        print(f"message_thread_id: {topic.message_thread_id}")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
