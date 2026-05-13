from __future__ import annotations

import asyncio

from aiogram import Bot

from _common import load_settings


async def check() -> int:
    settings = load_settings()
    from bot.errors import SupportChatConfigError
    from bot.services.topics import validate_support_chat

    if "replace_with" in settings.bot_token or settings.support_chat_id == 0:
        print("Replace BOT_TOKEN and SUPPORT_CHAT_ID in .env before calling Telegram.")
        return 1

    bot = Bot(token=settings.bot_token)
    try:
        print(f"support_chat_id: {settings.support_chat_id}")
        chat = await bot.get_chat(settings.support_chat_id)
        print(f"chat_id: {chat.id}")
        print(f"chat_type: {chat.type}")
        print(f"chat_title: {getattr(chat, 'title', '')}")
        print(f"chat_is_forum: {getattr(chat, 'is_forum', None)}")
        bot_user = await bot.get_me()
        print(f"bot_id: {bot_user.id}")
        print(f"bot_username: {bot_user.username}")
        member = await bot.get_chat_member(settings.support_chat_id, bot_user.id)
        print(f"bot_member_status: {member.status}")
        print(f"bot_can_manage_topics: {getattr(member, 'can_manage_topics', None)}")
        try:
            await validate_support_chat(bot, settings.support_chat_id)
        except SupportChatConfigError as exc:
            print(f"support_chat: failed: {exc}")
            return 1
        print("support_chat: ok")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
