from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from importlib import import_module
from inspect import Parameter, isawaitable, signature
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.errors import SupportChatConfigError, SupportTopicLifecycleError, SupportTopicNotFoundError
from database import config as database_config


STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
CLOSE_CALLBACK_PREFIX = "close_ticket:"
STATUS_PREFIXES = {
    STATUS_OPEN: "\U0001f7e2",
    STATUS_CLOSED: "\U0001f534",
}
TELEGRAM_FORUM_TOPIC_NAME_LIMIT = 128


@asynccontextmanager
async def open_database_session() -> AsyncIterator[object]:
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        await _call_optional_session_method(session, "rollback")
        raise
    else:
        await _call_optional_session_method(session, "commit")
    finally:
        await _call_optional_session_method(session, "close")


async def get_or_create_support_topic(
    session: object,
    bot: Bot,
    message: Message,
    support_chat_id: int,
) -> object:
    return await ensure_support_topic_for_message(session, bot, message, support_chat_id)


async def ensure_support_topic_for_message(
    session: object,
    bot: Bot,
    message: Message,
    support_chat_id: int,
) -> object:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, message.from_user.id)

    topic = await repository.get_by_user_id(session, message.from_user.id)
    if topic is not None:
        was_open = _is_topic_open(topic)
        topic = await _call_touch_support_topic(
            repository,
            session=session,
            topic=topic,
            message=message,
            status=None if was_open else STATUS_OPEN,
        )
        if was_open:
            try:
                await _rename_forum_topic(
                    bot,
                    support_chat_id,
                    int(topic.topic_id),
                    _topic_name(message, STATUS_OPEN),
                )
            except SupportTopicNotFoundError:
                topic = await recreate_support_topic(session, bot, message, support_chat_id, topic)
            return topic

        try:
            await _rename_forum_topic(
                bot,
                support_chat_id,
                int(topic.topic_id),
                _topic_name(message, STATUS_OPEN),
            )
            await refresh_topic_header(bot, message, support_chat_id, int(topic.topic_id))
        except SupportTopicNotFoundError:
            topic = await recreate_support_topic(session, bot, message, support_chat_id, topic)
        return topic

    forum_topic = await _create_forum_topic(bot, message, support_chat_id)
    await refresh_topic_header(bot, message, support_chat_id, int(forum_topic.message_thread_id))
    return await _call_create_support_topic(
        repository,
        session=session,
        user_id=message.from_user.id,
        topic_id=forum_topic.message_thread_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        status=STATUS_OPEN,
    )


async def refresh_topic_header(
    bot: Bot,
    message: Message,
    support_chat_id: int,
    topic_id: int,
) -> Message:
    try:
        header = await bot.send_message(
            chat_id=support_chat_id,
            message_thread_id=topic_id,
            text=_topic_header_text(message),
            disable_web_page_preview=True,
            reply_markup=topic_close_keyboard(topic_id),
        )
        await _unpin_all_forum_topic_messages(bot, support_chat_id, topic_id)
        await bot.pin_chat_message(
            chat_id=support_chat_id,
            message_id=header.message_id,
            disable_notification=True,
        )
    except TelegramBadRequest as exc:
        _raise_readable_topic_error(exc)
    return header


async def recreate_support_topic(
    session: object,
    bot: Bot,
    message: Message,
    support_chat_id: int,
    topic: object,
) -> object:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, message.from_user.id)

    persistent_topic = await repository.get_by_user_id(session, message.from_user.id)
    if persistent_topic is None:
        forum_topic = await _create_forum_topic(bot, message, support_chat_id)
        await refresh_topic_header(bot, message, support_chat_id, int(forum_topic.message_thread_id))
        return await _call_create_support_topic(
            repository,
            session=session,
            user_id=message.from_user.id,
            topic_id=forum_topic.message_thread_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            status=STATUS_OPEN,
        )

    await _call_touch_support_topic(
        repository,
        session=session,
        topic=persistent_topic,
        message=message,
        status=STATUS_OPEN,
    )
    persistent_topic = await _recreate_support_topic(
        repository,
        session=session,
        bot=bot,
        message=message,
        support_chat_id=support_chat_id,
        topic=persistent_topic,
    )
    await refresh_topic_header(bot, message, support_chat_id, int(persistent_topic.topic_id))
    return persistent_topic


async def get_by_topic_id(session: object, topic_id: int) -> Optional[object]:
    return await _repositories().get_by_topic_id(session, topic_id)


async def close_support_topic(
    session: object,
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
) -> Optional[object]:
    repository = _repositories()
    topic = await repository.get_by_topic_id(session, topic_id)
    if topic is None:
        return None
    if _is_topic_closed(topic):
        await _try_rename_forum_topic(
            bot,
            support_chat_id,
            int(topic.topic_id),
            _topic_name_from_topic(topic, STATUS_CLOSED),
        )
        return topic
    topic = await _call_update_support_topic(
        repository,
        session=session,
        topic=topic,
        status=STATUS_CLOSED,
    )
    await _try_rename_forum_topic(
        bot,
        support_chat_id,
        int(topic.topic_id),
        _topic_name_from_topic(topic, STATUS_CLOSED),
    )
    return topic


async def close_all_support_topics(
    session: object,
    bot: Bot,
    support_chat_id: int,
) -> Tuple[int, int]:
    repository = _repositories()
    get_open_topics = getattr(repository, "get_open_topics", None)
    if get_open_topics is None:
        raise SupportTopicLifecycleError(
            "Cannot close all support topics: repository.get_open_topics is missing."
        )

    topics = await _call_filtered(get_open_topics, session=session)
    closed_count = 0
    renamed_count = 0
    for topic in topics:
        topic = await _call_update_support_topic(
            repository,
            session=session,
            topic=topic,
            status=STATUS_CLOSED,
        )
        closed_count += 1
        renamed = await _try_rename_forum_topic(
            bot,
            support_chat_id,
            int(topic.topic_id),
            _topic_name_from_topic(topic, STATUS_CLOSED),
        )
        if renamed:
            renamed_count += 1

    return closed_count, renamed_count


def topic_close_keyboard(topic_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Закрыть обращение",
                    callback_data=build_close_callback_data(topic_id),
                )
            ]
        ]
    )


def build_close_callback_data(topic_id: int) -> str:
    return f"{CLOSE_CALLBACK_PREFIX}{topic_id}"


def is_close_ticket_callback(data: Optional[str]) -> bool:
    return bool(data and data.startswith(CLOSE_CALLBACK_PREFIX))


def topic_id_from_close_callback(data: str) -> int:
    return int(data[len(CLOSE_CALLBACK_PREFIX) :])


async def _call_optional_session_method(session: object, name: str) -> None:
    method = getattr(session, name, None)
    if method is None:
        return

    result = method()
    if isawaitable(result):
        await result


def _get_session_factory() -> Callable[[], object]:
    return database_config.async_session_maker


async def _call_create_support_topic(repository: object, **values: object) -> object:
    create = repository.create_support_topic
    params = signature(create).parameters
    if any(param.kind == Parameter.VAR_KEYWORD for param in params.values()):
        return await create(**values)

    filtered = {key: value for key, value in values.items() if key in params}
    return await create(**filtered)


async def _call_touch_support_topic(
    repository: object,
    *,
    session: object,
    topic: object,
    message: Message,
    status: Optional[str] = None,
) -> object:
    touch = getattr(repository, "touch_support_topic", None)
    if touch is None or message.from_user is None:
        return topic

    values = {
        "session": session,
        "topic": topic,
        "username": message.from_user.username,
        "full_name": message.from_user.full_name,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "status": status,
    }
    return await _call_filtered(touch, **values)


async def _call_update_support_topic(
    repository: object,
    *,
    session: object,
    topic: object,
    status: str,
) -> object:
    update = getattr(repository, "update_support_topic", None)
    if update is None:
        raise SupportTopicLifecycleError(
            "Cannot update support topic status: repository.update_support_topic is missing."
        )
    return await _call_filtered(update, session=session, topic=topic, status=status)


async def _call_update_topic_id(
    repository: object,
    *,
    session: object,
    topic: object,
    topic_id: int,
) -> object:
    update_topic_id = getattr(repository, "update_topic_id", None)
    if update_topic_id is None:
        raise SupportTopicLifecycleError(
            "Cannot update recreated forum topic id: repository.update_topic_id is missing."
        )
    return await _call_filtered(
        update_topic_id,
        session=session,
        topic=topic,
        topic_id=topic_id,
    )


async def _call_filtered(function: Callable[..., object], **values: object) -> object:
    params = signature(function).parameters
    if any(param.kind == Parameter.VAR_KEYWORD for param in params.values()):
        result = function(**values)
    else:
        filtered = {key: value for key, value in values.items() if key in params}
        result = function(**filtered)

    if isawaitable(result):
        return await result
    return result


async def _create_forum_topic(bot: Bot, message: Message, support_chat_id: int) -> object:
    try:
        return await bot.create_forum_topic(
            chat_id=support_chat_id,
            name=_topic_name(message, STATUS_OPEN),
        )
    except TelegramBadRequest as exc:
        if _is_not_enough_rights(exc):
            raise SupportChatConfigError(
                "Bot cannot create forum topics. Grant the bot admin permission "
                "'Manage Topics' in the support supergroup."
            ) from exc
        raise SupportChatConfigError(
            "Cannot create Telegram forum topic. Check SUPPORT_CHAT_ID: "
            "it must be the id of a supergroup with topics enabled, "
            "usually in -100... format."
        ) from exc


async def _recreate_support_topic(
    repository: object,
    *,
    session: object,
    bot: Bot,
    message: Message,
    support_chat_id: int,
    topic: object,
) -> object:
    forum_topic = await _create_forum_topic(bot, message, support_chat_id)
    return await _call_update_topic_id(
        repository,
        session=session,
        topic=topic,
        topic_id=int(forum_topic.message_thread_id),
    )


async def _unpin_all_forum_topic_messages(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
) -> None:
    unpin_all = getattr(bot, "unpin_all_forum_topic_messages", None)
    if unpin_all is not None:
        await unpin_all(chat_id=support_chat_id, message_thread_id=topic_id)
        return

    raise SupportTopicLifecycleError(
        "Cannot unpin forum topic messages: Bot.unpin_all_forum_topic_messages is unavailable."
    )


async def _rename_forum_topic(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
    name: str,
) -> None:
    edit_forum_topic = getattr(bot, "edit_forum_topic", None)
    if edit_forum_topic is None:
        raise SupportTopicLifecycleError(
            "Cannot rename forum topic: Bot.edit_forum_topic is unavailable."
        )

    try:
        await edit_forum_topic(
            chat_id=support_chat_id,
            message_thread_id=topic_id,
            name=name,
        )
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return
        _raise_readable_topic_error(exc)


async def _try_rename_forum_topic(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
    name: str,
) -> bool:
    try:
        await _rename_forum_topic(bot, support_chat_id, topic_id, name)
    except SupportTopicNotFoundError:
        return False
    return True


def _topic_header_text(message: Message) -> str:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    user = message.from_user
    username = f"@{user.username}" if user.username else "-"
    return "\n".join(
        (
            f"ID: {user.id}",
            f"Name: {user.full_name}",
            f"Login: {username}",
            f"Dialog: tg://user?id={user.id}",
            f"UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "___________",
        )
    )


def _topic_name(message: Message, status: str) -> str:
    user = message.from_user
    if user is None:
        return format_topic_name(status, None, None, message.chat.id)

    return format_topic_name(status, user.full_name, user.username, user.id)


def _topic_name_from_topic(topic: object, status: str) -> str:
    return format_topic_name(
        status,
        getattr(topic, "full_name", None),
        getattr(topic, "username", None),
        int(getattr(topic, "user_id")),
    )


def format_topic_name(
    status: str,
    full_name: Optional[str],
    username: Optional[str],
    user_id: int,
) -> str:
    prefix = STATUS_PREFIXES.get(status, STATUS_PREFIXES[STATUS_OPEN])
    clean_full_name = _clean_text(full_name)
    identity = _topic_identity(username, user_id)

    if clean_full_name:
        body = f"{clean_full_name} ({identity})"
    else:
        body = identity

    return f"{prefix} {body}"[:TELEGRAM_FORUM_TOPIC_NAME_LIMIT]


def _topic_identity(username: Optional[str], user_id: int) -> str:
    clean_username = _clean_text(username)
    if clean_username:
        return f"@{clean_username.lstrip('@')}"
    return str(user_id)


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value


def _repositories() -> object:
    return import_module("database.repositories")


def _is_topic_open(topic: object) -> bool:
    return getattr(topic, "status", None) == STATUS_OPEN


def _is_topic_closed(topic: object) -> bool:
    return getattr(topic, "status", None) == STATUS_CLOSED


def _raise_readable_topic_error(exc: TelegramBadRequest) -> None:
    if _is_message_thread_not_found(exc):
        raise SupportTopicNotFoundError(
            "Telegram BadRequest: support forum topic was not found or became invalid. "
            "The support forum topic was probably deleted."
        ) from exc
    if _is_not_enough_rights(exc):
        raise SupportChatConfigError(
            "Bot cannot manage forum topic messages. Grant admin permissions "
            "'Manage Topics', 'Pin Messages' and 'Send Messages' in the support supergroup."
        ) from exc
    if "chat not found" in _error_text(exc):
        raise SupportChatConfigError(
            "Cannot access SUPPORT_CHAT_ID. Check that the id is correct "
            "and the bot is a member of the support supergroup."
        ) from exc
    raise exc


def _is_message_thread_not_found(exc: BaseException) -> bool:
    text = _error_text(exc)
    return "message thread not found" in text or "topic_id_invalid" in text


def _is_not_enough_rights(exc: BaseException) -> bool:
    text = _error_text(exc)
    return "not enough rights" in text or "have no rights" in text


def _is_not_modified(exc: BaseException) -> bool:
    text = _error_text(exc)
    return "not modified" in text or "topic_not_modified" in text


def _error_text(exc: BaseException) -> str:
    return str(exc).lower()


async def validate_support_chat(bot: Bot, support_chat_id: int) -> None:
    try:
        chat = await bot.get_chat(support_chat_id)
    except TelegramBadRequest as exc:
        raise SupportChatConfigError(
            "Cannot access SUPPORT_CHAT_ID. Check that the id is correct "
            "and usually starts with -100 for a supergroup."
        ) from exc
    except TelegramForbiddenError as exc:
        raise SupportChatConfigError(
            "Bot has no access to SUPPORT_CHAT_ID. Add the bot to the support chat "
            "and grant admin permissions."
        ) from exc

    chat_type = str(chat.type)
    is_forum = bool(getattr(chat, "is_forum", False))
    if chat_type != "supergroup" or not is_forum:
        title = getattr(chat, "title", "")
        raise SupportChatConfigError(
            "SUPPORT_CHAT_ID does not point to a forum supergroup. "
            f"Resolved chat: id={chat.id}, type={chat_type}, is_forum={is_forum}, title={title!r}."
        )

    bot_user = await bot.get_me()
    member = await bot.get_chat_member(support_chat_id, bot_user.id)
    member_status = str(member.status)
    if member_status not in {"administrator", "creator"}:
        raise SupportChatConfigError(
            "Bot is not an administrator in SUPPORT_CHAT_ID. "
            "Promote the bot to administrator and grant 'Manage Topics'."
        )

    if member_status == "administrator" and not bool(getattr(member, "can_manage_topics", False)):
        raise SupportChatConfigError(
            "Bot is administrator, but lacks 'Manage Topics'. "
            "Enable this permission in the support supergroup admin settings."
        )
