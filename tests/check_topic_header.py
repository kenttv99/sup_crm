from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from _common import load_settings, parse_int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check support topic header pinning.")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--topic-id", type=int, default=None)
    args = parser.parse_args()

    if args.user_id is None and os.getenv("TEST_USER_ID"):
        args.user_id = parse_int(os.getenv("TEST_USER_ID"), "TEST_USER_ID")
    if args.topic_id is None and os.getenv("TEST_TOPIC_ID"):
        args.topic_id = parse_int(os.getenv("TEST_TOPIC_ID"), "TEST_TOPIC_ID")
    return args


def build_info_message(user_id: Optional[int], topic_id: int) -> str:
    user_text = str(user_id) if user_id is not None else "unknown"
    return "\n".join(
        (
            f"ID: {user_text}",
            "Name: Manual Check",
            "Login: -",
            f"Dialog: tg://user?id={user_text}",
            f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "___________",
        )
    )


async def create_temp_topic(bot: Bot, support_chat_id: int) -> int:
    title = f"manual-header-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    topic = await bot.create_forum_topic(chat_id=support_chat_id, name=title)
    print(f"created_topic_title: {title}")
    print(f"created_topic_id: {topic.message_thread_id}")
    return int(topic.message_thread_id)


async def check() -> int:
    args = parse_args()
    settings = load_settings()
    if "replace_with" in settings.bot_token or settings.support_chat_id == 0:
        print("Replace BOT_TOKEN and SUPPORT_CHAT_ID in .env before calling Telegram.")
        return 1

    bot = Bot(token=settings.bot_token)
    try:
        topic_id = args.topic_id
        if topic_id is None:
            try:
                topic_id = await create_temp_topic(bot, settings.support_chat_id)
            except TelegramBadRequest as exc:
                print(f"create_forum_topic: failed: {exc}")
                return 1

        text = build_info_message(args.user_id, topic_id)
        print("info_message:")
        print(text)

        try:
            message = await bot.send_message(
                chat_id=settings.support_chat_id,
                message_thread_id=topic_id,
                text=text,
            )
            print(f"info_message_id: {message.message_id}")

            unpinned = await bot.unpin_all_forum_topic_messages(
                chat_id=settings.support_chat_id,
                message_thread_id=topic_id,
            )
            print(f"unpin_all_forum_topic_messages: {unpinned}")

            pinned = await bot.pin_chat_message(
                chat_id=settings.support_chat_id,
                message_id=message.message_id,
                disable_notification=True,
            )
            print(f"pin_chat_message: {pinned}")
        except TelegramBadRequest as exc:
            print(f"topic_header_check: failed: {exc}")
            return 1

        print("topic_header_check: ok")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
