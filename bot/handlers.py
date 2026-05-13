from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.services.topics import (
    get_by_topic_id,
    get_or_create_support_topic,
    open_database_session,
    recreate_support_topic,
)
from config.settings import Settings

router = Router(name="support")


@router.message(F.chat.type == "private", CommandStart())
async def start(message: Message) -> None:
    await message.answer("Напишите сообщение, оператор ответит здесь.")


@router.message(F.chat.type == "private")
async def forward_private_message(message: Message, bot: Bot, settings: Settings) -> None:
    if should_ignore_private_message(message):
        return

    topic = await get_private_support_topic(message, bot, settings)
    try:
        await copy_private_message_to_topic(message, bot, settings, topic)
    except TelegramBadRequest as exc:
        if not is_message_thread_not_found(exc):
            raise
        topic = await recreate_private_support_topic(message, bot, settings, topic)
        await copy_private_message_to_topic(message, bot, settings, topic)


@router.message(F.chat.id)
async def forward_support_topic_message(message: Message, bot: Bot, settings: Settings) -> None:
    if message.chat.id != settings.support_chat_id:
        return
    if message.message_thread_id is None:
        return
    if should_ignore_support_message(message, settings):
        return

    async with open_database_session() as session:
        user = await get_by_topic_id(session, message.message_thread_id)
    if user is None:
        print(
            "Support topic message ignored: topic_id "
            f"{message.message_thread_id} was not found in database."
        )
        return

    await bot.copy_message(
        chat_id=get_user_id(user),
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )


def should_ignore_private_message(message: Message) -> bool:
    if message.chat.type != "private":
        return True
    if message.from_user is None or message.from_user.is_bot:
        return True
    return is_slash_command(message) or is_service_message(message)


def should_ignore_support_message(message: Message, settings: Settings) -> bool:
    if message.from_user is None or message.from_user.is_bot:
        return True
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        print(
            "Support topic message ignored: operator "
            f"{message.from_user.id} is not listed in ADMIN_IDS."
        )
        return True
    return is_slash_command(message) or is_service_message(message)


def is_slash_command(message: Message) -> bool:
    text = message.text or message.caption
    return bool(text and text.startswith("/"))


def is_service_message(message: Message) -> bool:
    return bool(
        message.new_chat_members
        or message.left_chat_member
        or message.new_chat_title
        or message.new_chat_photo
        or message.delete_chat_photo
        or message.group_chat_created
        or message.supergroup_chat_created
        or message.channel_chat_created
        or message.message_auto_delete_timer_changed
        or message.forum_topic_created
        or message.forum_topic_edited
        or message.forum_topic_closed
        or message.forum_topic_reopened
        or message.general_forum_topic_hidden
        or message.general_forum_topic_unhidden
        or message.pinned_message
    )


async def get_private_support_topic(message: Message, bot: Bot, settings: Settings) -> object:
    async with open_database_session() as session:
        return await get_or_create_support_topic(
            session,
            bot,
            message,
            settings.support_chat_id,
        )


async def copy_private_message_to_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    topic: object,
) -> None:
    await bot.copy_message(
        chat_id=settings.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=get_topic_id(topic),
    )


async def recreate_private_support_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    topic: object,
) -> object:
    async with open_database_session() as session:
        return await recreate_support_topic(
            session,
            bot,
            message,
            settings.support_chat_id,
            topic,
        )


def is_message_thread_not_found(exc: TelegramBadRequest) -> bool:
    return "message thread not found" in str(exc).lower()


def get_topic_id(topic: object) -> int:
    return int(topic.topic_id)


def get_user_id(user: object) -> int:
    return int(user.user_id)
