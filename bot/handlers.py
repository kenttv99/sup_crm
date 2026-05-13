from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.services.topics import (
    close_all_support_topics,
    close_support_topic,
    get_by_topic_id,
    get_or_create_support_topic,
    is_close_ticket_callback,
    open_database_session,
    recreate_support_topic,
    topic_id_from_close_callback,
)
from config.settings import Settings

router = Router(name="support")
TICKET_CLOSED_MESSAGE = "Обращение закрыто. Следующее сообщение пользователя откроет новое обращение."


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


@router.callback_query(lambda callback: is_close_ticket_callback(callback.data))
async def close_support_topic_callback(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    if not is_allowed_operator(callback.from_user.id, settings):
        await callback.answer("Нет прав на закрытие обращения.", show_alert=True)
        return

    if not isinstance(callback.message, Message):
        await callback.answer("Не удалось определить topic.", show_alert=True)
        return
    if callback.message.chat.id != settings.support_chat_id:
        await callback.answer("Кнопка доступна только в чате поддержки.", show_alert=True)
        return

    topic_id = topic_id_from_close_callback(callback.data or "")
    found, changed = await close_topic(topic_id, bot, settings)
    if not found:
        await callback.answer("Topic не найден в базе.", show_alert=True)
        return
    if not changed:
        await callback.answer("Обращение уже закрыто.")
        return

    await callback.answer("Обращение закрыто.")
    await send_topic_status_message(bot, settings, topic_id, TICKET_CLOSED_MESSAGE)


@router.message(F.chat.id, Command("end"))
async def close_support_topic_command(message: Message, bot: Bot, settings: Settings) -> None:
    if message.chat.id != settings.support_chat_id:
        return
    if message.message_thread_id is None:
        await message.answer("Команда /end должна быть отправлена внутри topic обращения.")
        return
    if message.from_user is None or not is_allowed_operator(message.from_user.id, settings):
        await message.answer("Нет прав на закрытие обращения.")
        return

    found, changed = await close_topic(message.message_thread_id, bot, settings)
    if not found:
        await message.answer("Topic не найден в базе.")
        return
    if not changed:
        await message.answer("Обращение уже закрыто.")
        return

    await send_topic_status_message(bot, settings, message.message_thread_id, TICKET_CLOSED_MESSAGE)


@router.message(F.chat.id, Command("all_end"))
async def close_all_support_topics_command(message: Message, bot: Bot, settings: Settings) -> None:
    if message.chat.id != settings.support_chat_id:
        return
    if message.message_thread_id is None:
        await message.answer("Команда /all_end должна быть отправлена внутри topic поддержки.")
        return
    if message.from_user is None or not is_allowed_operator(message.from_user.id, settings):
        await message.answer("Нет прав на закрытие обращений.")
        return

    async with open_database_session() as session:
        closed_count, renamed_count = await close_all_support_topics(
            session,
            bot,
            settings.support_chat_id,
        )
    await message.answer(f"Закрыто обращений: {closed_count}. Переименовано topic: {renamed_count}.")


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
    if not is_open_topic(user):
        print(
            "Support topic message ignored: topic_id "
            f"{message.message_thread_id} is not open."
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
    if not is_allowed_operator(message.from_user.id, settings):
        print(
            "Support topic message ignored: operator "
            f"{message.from_user.id} is not listed in ADMIN_IDS."
        )
        return True
    return is_slash_command(message) or is_service_message(message)


def is_allowed_operator(user_id: int, settings: Settings) -> bool:
    return not settings.admin_ids or user_id in settings.admin_ids


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


async def close_topic(topic_id: int, bot: Bot, settings: Settings) -> tuple:
    async with open_database_session() as session:
        topic = await get_by_topic_id(session, topic_id)
        if topic is None:
            return False, False
        was_open = is_open_topic(topic)
        await close_support_topic(session, bot, settings.support_chat_id, topic_id)
    return True, was_open


async def send_topic_status_message(
    bot: Bot,
    settings: Settings,
    topic_id: int,
    text: str,
) -> None:
    try:
        await bot.send_message(
            chat_id=settings.support_chat_id,
            message_thread_id=topic_id,
            text=text,
        )
    except TelegramBadRequest as exc:
        print(f"Cannot send topic status message to topic_id {topic_id}: {exc}")


def is_message_thread_not_found(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return "message thread not found" in text or "topic_id_invalid" in text


def get_topic_id(topic: object) -> int:
    return int(topic.topic_id)


def get_user_id(user: object) -> int:
    return int(user.user_id)


def is_open_topic(topic: object) -> bool:
    return getattr(topic, "status", None) == "open"
