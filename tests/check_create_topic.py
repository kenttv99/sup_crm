from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from _common import load_settings


async def check() -> int:
    settings = load_settings()
    if "replace_with" in settings.bot_token or settings.support_chat_id == 0:
        print("Replace BOT_TOKEN and SUPPORT_CHAT_ID in .env before calling Telegram.")
        return 1

    title = f"manual-check-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    bot = Bot(token=settings.bot_token)
    try:
        try:
            topic = await bot.create_forum_topic(chat_id=settings.support_chat_id, name=title)
        except TelegramBadRequest as exc:
            print(f"create_forum_topic: failed: {exc}")
            return 1
        print(f"title: {title}")
        print(f"message_thread_id: {topic.message_thread_id}")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
