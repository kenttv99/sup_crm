from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib import import_module
from inspect import Parameter, isawaitable, signature
import logging
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.errors import SupportChatConfigError, SupportTopicLifecycleError, SupportTopicNotFoundError
from database import config as database_config

logger = logging.getLogger(__name__)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

TICKET_NEW = "new"
TICKET_IN_PROGRESS = "in_progress"
TICKET_WAITING_USER = "waiting_user"
TICKET_CLOSED = "closed"

CLOSE_CALLBACK_PREFIX = "close_ticket:"
TAKE_CALLBACK_PREFIX = "take_ticket:"

STATUS_PREFIXES = {
    STATUS_OPEN: "\U0001f7e2",
    TICKET_NEW: "\U0001f7e2",
    TICKET_IN_PROGRESS: "\U0001f7e1",
    TICKET_WAITING_USER: "\U0001f535",
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
    *,
    source_payload: Optional[str] = None,
) -> object:
    return await ensure_support_topic_for_message(
        session,
        bot,
        message,
        support_chat_id,
        source_payload=source_payload,
    )


async def ensure_support_topic_for_message(
    session: object,
    bot: Bot,
    message: Message,
    support_chat_id: int,
    *,
    source_payload: Optional[str] = None,
) -> object:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, message.from_user.id)

    user = message.from_user
    topic = await repository.get_by_user_id(session, user.id)
    if topic is not None:
        was_open = _is_topic_open(topic)
        old_title = _topic_name_from_topic(
            topic,
            STATUS_OPEN if was_open else STATUS_CLOSED,
        )
        topic = await _call_touch_support_topic(
            repository,
            session=session,
            topic=topic,
            message=message,
            status=None if was_open else STATUS_OPEN,
        )

        ticket = await _ensure_active_ticket(
            repository,
            session,
            user_id=user.id,
            topic_id=int(topic.topic_id),
            source_payload=source_payload,
        )
        effective_status = getattr(ticket, "status", STATUS_OPEN) if ticket else STATUS_OPEN
        new_title = _topic_name(message, effective_status)

        if was_open:
            if old_title != new_title:
                try:
                    await _rename_forum_topic(bot, support_chat_id, int(topic.topic_id), new_title)
                except SupportTopicNotFoundError:
                    topic = await recreate_support_topic(
                        session, bot, message, support_chat_id, topic
                    )
            return topic

        try:
            await _try_reopen_forum_topic(bot, support_chat_id, int(topic.topic_id))
            if old_title != new_title:
                await _rename_forum_topic(bot, support_chat_id, int(topic.topic_id), new_title)
            header = await refresh_topic_header_for_user(
                bot=bot,
                support_chat_id=support_chat_id,
                topic_id=int(topic.topic_id),
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                ticket=ticket,
                notes=getattr(topic, "notes", None),
            )
            await _call_update_support_topic(
                repository,
                session=session,
                topic=topic,
                header_message_id=header.message_id,
            )
        except SupportTopicNotFoundError:
            topic = await recreate_support_topic(session, bot, message, support_chat_id, topic)
        return topic

    forum_topic = await _create_forum_topic(bot, message, support_chat_id)
    topic_id = int(forum_topic.message_thread_id)
    topic = await _call_create_support_topic(
        repository,
        session=session,
        user_id=user.id,
        topic_id=topic_id,
        username=user.username,
        full_name=user.full_name,
        first_name=user.first_name,
        last_name=user.last_name,
        status=STATUS_OPEN,
    )
    ticket = await _ensure_active_ticket(
        repository,
        session,
        user_id=user.id,
        topic_id=topic_id,
        source_payload=source_payload,
    )
    header = await refresh_topic_header_for_user(
        bot=bot,
        support_chat_id=support_chat_id,
        topic_id=topic_id,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        ticket=ticket,
        notes=getattr(topic, "notes", None),
    )
    await _call_update_support_topic(
        repository,
        session=session,
        topic=topic,
        header_message_id=header.message_id,
    )
    return topic


async def refresh_topic_header(
    bot: Bot,
    message: Message,
    support_chat_id: int,
    topic_id: int,
    *,
    ticket: Optional[object] = None,
    notes: Optional[str] = None,
) -> Message:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    return await refresh_topic_header_for_user(
        bot=bot,
        support_chat_id=support_chat_id,
        topic_id=topic_id,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        ticket=ticket,
        notes=notes,
    )


async def refresh_topic_header_for_user(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    *,
    ticket: Optional[object] = None,
    notes: Optional[str] = None,
) -> Message:
    ticket_id = getattr(ticket, "id", None) if ticket is not None else None
    assigned_id = getattr(ticket, "assigned_operator_id", None) if ticket is not None else None
    try:
        header = await bot.send_message(
            chat_id=support_chat_id,
            message_thread_id=topic_id,
            text=_topic_header_text_for_user(
                user_id,
                username,
                full_name,
                ticket=ticket,
                notes=notes,
            ),
            disable_web_page_preview=True,
            reply_markup=topic_close_keyboard(
                topic_id,
                ticket_id=ticket_id,
                assigned=assigned_id is not None,
            ),
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

    user = message.from_user
    persistent_topic = await repository.get_by_user_id(session, user.id)
    if persistent_topic is None:
        forum_topic = await _create_forum_topic(bot, message, support_chat_id)
        topic_id = int(forum_topic.message_thread_id)
        persistent_topic = await _call_create_support_topic(
            repository,
            session=session,
            user_id=user.id,
            topic_id=topic_id,
            username=user.username,
            full_name=user.full_name,
            first_name=user.first_name,
            last_name=user.last_name,
            status=STATUS_OPEN,
        )
        ticket = await _ensure_active_ticket(
            repository, session, user_id=user.id, topic_id=topic_id
        )
        header = await refresh_topic_header(
            bot, message, support_chat_id, topic_id, ticket=ticket
        )
        await _call_update_support_topic(
            repository, session=session, topic=persistent_topic, header_message_id=header.message_id
        )
        return persistent_topic

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
    new_topic_id = int(persistent_topic.topic_id)
    ticket = await _ensure_active_ticket(
        repository, session, user_id=user.id, topic_id=new_topic_id
    )
    if ticket is not None and getattr(ticket, "topic_id", None) != new_topic_id:
        await repository.update_ticket(session, ticket, topic_id=new_topic_id)
    header = await refresh_topic_header(
        bot,
        message,
        support_chat_id,
        new_topic_id,
        ticket=ticket,
        notes=getattr(persistent_topic, "notes", None),
    )
    await _call_update_support_topic(
        repository, session=session, topic=persistent_topic, header_message_id=header.message_id
    )
    return persistent_topic


async def get_by_topic_id(session: object, topic_id: int) -> Optional[object]:
    return await _repositories().get_by_topic_id(session, topic_id)


async def close_support_topic(
    session: object,
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
    *,
    closed_by: str = "operator",
    actor_type: str = "operator",
    actor_id: Optional[int] = None,
    close_forum: bool = True,
) -> Optional[object]:
    repository = _repositories()
    topic = await repository.get_by_topic_id(session, topic_id)
    if topic is None:
        return None

    user_id = int(topic.user_id)
    if _is_topic_closed(topic):
        await _try_rename_forum_topic(
            bot,
            support_chat_id,
            int(topic.topic_id),
            _topic_name_from_topic(topic, STATUS_CLOSED),
        )
        return topic

    await _clear_header_keyboard(
        bot, support_chat_id, getattr(topic, "header_message_id", None)
    )
    topic = await _call_update_support_topic(
        repository,
        session=session,
        topic=topic,
        status=STATUS_CLOSED,
    )
    close_ticket = getattr(repository, "close_active_ticket", None)
    if close_ticket is not None:
        await close_ticket(
            session,
            user_id=user_id,
            closed_by=closed_by,
            actor_type=actor_type,
            actor_id=actor_id,
        )

    await _try_rename_forum_topic(
        bot,
        support_chat_id,
        int(topic.topic_id),
        _topic_name_from_topic(topic, STATUS_CLOSED),
    )
    if close_forum:
        await _try_close_forum_topic(bot, support_chat_id, int(topic.topic_id))
    return topic


async def close_support_topic_by_user(
    session: object,
    bot: Bot,
    support_chat_id: int,
    user_id: int,
) -> Tuple[Optional[object], bool]:
    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, user_id)

    topic = await repository.get_by_user_id(session, user_id)
    if topic is None:
        return None, False

    was_open = _is_topic_open(topic)
    topic = await close_support_topic(
        session,
        bot,
        support_chat_id,
        int(topic.topic_id),
        closed_by="user",
        actor_type="user",
        actor_id=user_id,
    )
    return topic, was_open


async def reopen_support_topic_by_user(
    session: object,
    bot: Bot,
    support_chat_id: int,
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    *,
    actor_type: str = "user",
    actor_id: Optional[int] = None,
) -> Tuple[object, bool]:
    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, user_id)

    topic = await repository.get_by_user_id(session, user_id)
    if topic is None:
        forum_topic = await _create_forum_topic_for_user(
            bot, support_chat_id, user_id, username, full_name
        )
        topic_id = int(forum_topic.message_thread_id)
        topic = await _call_create_support_topic(
            repository,
            session=session,
            user_id=user_id,
            topic_id=topic_id,
            username=username,
            full_name=full_name,
            status=STATUS_OPEN,
        )
        ticket = await _ensure_active_ticket(
            repository,
            session,
            user_id=user_id,
            topic_id=topic_id,
            actor_type=actor_type,
            actor_id=actor_id or user_id,
        )
        header = await refresh_topic_header_for_user(
            bot,
            support_chat_id,
            topic_id,
            user_id,
            username,
            full_name,
            ticket=ticket,
        )
        await _call_update_support_topic(
            repository, session=session, topic=topic, header_message_id=header.message_id
        )
        return topic, True

    was_open = _is_topic_open(topic)
    old_title = _topic_name_from_topic(topic, STATUS_OPEN if was_open else STATUS_CLOSED)
    topic = await _call_update_support_topic(
        repository,
        session=session,
        topic=topic,
        username=username,
        full_name=full_name,
        status=None if was_open else STATUS_OPEN,
    )
    ticket = await _ensure_active_ticket(
        repository,
        session,
        user_id=user_id,
        topic_id=int(topic.topic_id),
        actor_type=actor_type,
        actor_id=actor_id or user_id,
    )
    new_title = _topic_name_from_topic(
        topic, getattr(ticket, "status", STATUS_OPEN) if ticket else STATUS_OPEN
    )

    try:
        if not was_open:
            await _try_reopen_forum_topic(bot, support_chat_id, int(topic.topic_id))
        if old_title != new_title or not was_open:
            await _rename_forum_topic(bot, support_chat_id, int(topic.topic_id), new_title)
        if not was_open:
            header = await refresh_topic_header_for_user(
                bot,
                support_chat_id,
                int(topic.topic_id),
                user_id,
                username,
                full_name,
                ticket=ticket,
                notes=getattr(topic, "notes", None),
            )
            await _call_update_support_topic(
                repository, session=session, topic=topic, header_message_id=header.message_id
            )
    except SupportTopicNotFoundError:
        topic = await _recreate_support_topic_for_user(
            repository,
            session=session,
            bot=bot,
            support_chat_id=support_chat_id,
            topic=topic,
            user_id=user_id,
            username=username,
            full_name=full_name,
        )
        new_tid = int(topic.topic_id)
        if ticket is not None:
            await repository.update_ticket(session, ticket, topic_id=new_tid)
        header = await refresh_topic_header_for_user(
            bot,
            support_chat_id,
            new_tid,
            user_id,
            username,
            full_name,
            ticket=ticket,
            notes=getattr(topic, "notes", None),
        )
        await _call_update_support_topic(
            repository, session=session, topic=topic, header_message_id=header.message_id
        )
        return topic, True

    return topic, not was_open


async def close_all_support_topics(
    session: object,
    bot: Bot,
    support_chat_id: int,
    *,
    operator_id: Optional[int] = None,
) -> Tuple[int, int, Tuple[int, ...]]:
    repository = _repositories()
    get_open_topics = getattr(repository, "get_open_topics", None)
    if get_open_topics is None:
        raise SupportTopicLifecycleError(
            "Cannot close all support topics: repository.get_open_topics is missing."
        )

    topics = await _call_filtered(get_open_topics, session=session, lock=True)
    closed_count = 0
    renamed_count = 0
    user_ids = []
    close_ticket = getattr(repository, "close_active_ticket", None)
    closed_by = f"all_end:{operator_id}" if operator_id is not None else "all_end"

    for topic in topics:
        uid = int(topic.user_id)
        tid = int(topic.topic_id)
        user_ids.append(uid)
        await _clear_header_keyboard(
            bot, support_chat_id, getattr(topic, "header_message_id", None)
        )
        topic = await _call_update_support_topic(
            repository,
            session=session,
            topic=topic,
            status=STATUS_CLOSED,
        )
        if close_ticket is not None:
            await close_ticket(
                session,
                user_id=uid,
                closed_by=closed_by,
                actor_type="operator",
                actor_id=operator_id,
            )
        closed_count += 1
        renamed = await _try_rename_forum_topic(
            bot,
            support_chat_id,
            tid,
            _topic_name_from_topic(topic, STATUS_CLOSED),
        )
        await _try_close_forum_topic(bot, support_chat_id, tid)
        if renamed:
            renamed_count += 1

    return closed_count, renamed_count, tuple(user_ids)


async def sync_topic_status_from_ticket(
    bot: Bot,
    support_chat_id: int,
    topic: object,
    ticket_status: str,
) -> None:
    await _try_rename_forum_topic(
        bot,
        support_chat_id,
        int(topic.topic_id),
        _topic_name_from_topic(topic, ticket_status),
    )


def topic_close_keyboard(
    topic_id: int,
    *,
    ticket_id: Optional[int] = None,
    assigned: bool = False,
) -> InlineKeyboardMarkup:
    buttons = []
    if ticket_id is not None and not assigned:
        buttons.append(
            InlineKeyboardButton(
                text="Взять в работу",
                callback_data=f"{TAKE_CALLBACK_PREFIX}{topic_id}:{ticket_id}",
            )
        )
    buttons.append(
        InlineKeyboardButton(
            text="Закрыть обращение",
            callback_data=build_close_callback_data(topic_id, ticket_id=ticket_id),
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def build_close_callback_data(
    topic_id: int,
    *,
    ticket_id: Optional[int] = None,
) -> str:
    if ticket_id is not None:
        return f"{CLOSE_CALLBACK_PREFIX}{topic_id}:{ticket_id}"
    return f"{CLOSE_CALLBACK_PREFIX}{topic_id}"


def is_close_ticket_callback(data: Optional[str]) -> bool:
    return bool(data and data.startswith(CLOSE_CALLBACK_PREFIX))


def is_take_ticket_callback(data: Optional[str]) -> bool:
    return bool(data and data.startswith(TAKE_CALLBACK_PREFIX))


def topic_id_from_close_callback(data: str) -> int:
    payload = data[len(CLOSE_CALLBACK_PREFIX) :]
    return int(payload.split(":", 1)[0])


def parse_ticket_callback(data: str, prefix: str) -> Tuple[int, Optional[int]]:
    parts = data[len(prefix) :].split(":")
    topic_id = int(parts[0])
    ticket_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return topic_id, ticket_id


async def _ensure_active_ticket(
    repository: object,
    session: object,
    *,
    user_id: int,
    topic_id: int,
    source_payload: Optional[str] = None,
    actor_type: str = "user",
    actor_id: Optional[int] = None,
) -> Optional[object]:
    get_active = getattr(repository, "get_active_ticket_by_user", None)
    create_ticket = getattr(repository, "create_ticket", None)
    if get_active is None or create_ticket is None:
        return None

    ticket = await get_active(session, user_id)
    if ticket is not None:
        return ticket

    ticket = await create_ticket(
        session,
        user_id=user_id,
        topic_id=topic_id,
        source_payload=source_payload,
        status=TICKET_NEW,
    )
    record_event = getattr(repository, "record_ticket_event", None)
    if record_event is not None:
        await record_event(
            session,
            ticket_id=int(ticket.id),
            actor_type=actor_type,
            actor_id=actor_id or user_id,
            event_type="ticket_opened",
            new_value=TICKET_NEW,
        )
    return ticket


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
    **fields: object,
) -> object:
    update = getattr(repository, "update_support_topic", None)
    if update is None:
        raise SupportTopicLifecycleError(
            "Cannot update support topic status: repository.update_support_topic is missing."
        )
    filtered_fields = {k: v for k, v in fields.items() if v is not None or k in {"username", "full_name", "ban_reason", "notes"}}
    return await _call_filtered(
        update,
        session=session,
        topic=topic,
        **filtered_fields,
    )


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
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")
    return await _create_forum_topic_for_user(
        bot,
        support_chat_id,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )


async def _create_forum_topic_for_user(
    bot: Bot,
    support_chat_id: int,
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
) -> object:
    try:
        return await bot.create_forum_topic(
            chat_id=support_chat_id,
            name=_topic_name_for_user(STATUS_OPEN, full_name, username, user_id),
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


async def _recreate_support_topic_for_user(
    repository: object,
    *,
    session: object,
    bot: Bot,
    support_chat_id: int,
    topic: object,
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
) -> object:
    forum_topic = await _create_forum_topic_for_user(
        bot, support_chat_id, user_id, username, full_name
    )
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


async def _try_close_forum_topic(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
) -> None:
    close_fn = getattr(bot, "close_forum_topic", None)
    if close_fn is None:
        return
    try:
        await close_fn(chat_id=support_chat_id, message_thread_id=topic_id)
    except TelegramBadRequest as exc:
        if not (_is_not_modified(exc) or _is_message_thread_not_found(exc)):
            logger.warning("Cannot close forum topic %s: %s", topic_id, exc)


async def _try_reopen_forum_topic(
    bot: Bot,
    support_chat_id: int,
    topic_id: int,
) -> None:
    reopen_fn = getattr(bot, "reopen_forum_topic", None)
    if reopen_fn is None:
        return
    try:
        await reopen_fn(chat_id=support_chat_id, message_thread_id=topic_id)
    except TelegramBadRequest as exc:
        if _is_message_thread_not_found(exc):
            _raise_readable_topic_error(exc)
        if not _is_not_modified(exc):
            logger.warning("Cannot reopen forum topic %s: %s", topic_id, exc)


async def _clear_header_keyboard(
    bot: Bot,
    support_chat_id: int,
    header_message_id: Optional[int],
) -> None:
    if header_message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=support_chat_id,
            message_id=int(header_message_id),
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


def _topic_header_text(message: Message) -> str:
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")
    return _topic_header_text_for_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )


def _topic_header_text_for_user(
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    *,
    ticket: Optional[object] = None,
    notes: Optional[str] = None,
) -> str:
    login = f"@{username}" if username else "-"
    lines = [
        f"ID: {user_id}",
        f"Name: {full_name or '-'}",
        f"Login: {login}",
        f"Dialog: tg://user?id={user_id}",
        f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if ticket is not None:
        tid = getattr(ticket, "id", None)
        tstatus = getattr(ticket, "status", None)
        assigned = getattr(ticket, "assigned_operator_id", None)
        payload = getattr(ticket, "source_payload", None)
        tags = getattr(ticket, "tags", None)
        if tid is not None:
            lines.append(f"Ticket: #{tid} ({tstatus})")
        if assigned is not None:
            lines.append(f"Operator: {assigned}")
        if payload:
            lines.append(f"Source: {payload}")
        if tags:
            lines.append(f"Tags: {tags}")
    if notes:
        lines.append(f"Note: {notes}")
    lines.extend(["", "___________"])
    return "\n".join(lines)


def _topic_name(message: Message, status: str) -> str:
    user = message.from_user
    if user is None:
        return _topic_name_for_user(status, None, None, message.chat.id)
    return _topic_name_for_user(status, user.full_name, user.username, user.id)


def _topic_name_for_user(
    status: str,
    full_name: Optional[str],
    username: Optional[str],
    user_id: int,
) -> str:
    return format_topic_name(status, full_name, username, user_id)


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
    body = f"{clean_full_name} ({identity})" if clean_full_name else identity
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
    return value or None


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

    chat_type = _telegram_value(chat.type)
    is_forum = bool(getattr(chat, "is_forum", False))
    if chat_type != "supergroup" or not is_forum:
        title = getattr(chat, "title", "")
        raise SupportChatConfigError(
            "SUPPORT_CHAT_ID does not point to a forum supergroup. "
            f"Resolved chat: id={chat.id}, type={chat_type}, is_forum={is_forum}, title={title!r}."
        )

    bot_user = await bot.get_me()
    member = await bot.get_chat_member(support_chat_id, bot_user.id)
    member_status = _telegram_value(member.status)
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


def _telegram_value(value: object) -> str:
    return str(getattr(value, "value", value)).lower()
