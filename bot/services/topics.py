from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib import import_module
from inspect import Parameter, isawaitable, signature
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from bot.errors import SupportChatConfigError
from database import config as database_config


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
    if message.from_user is None:
        raise RuntimeError("Private support message must have from_user")

    repository = _repositories()
    advisory_lock = getattr(repository, "advisory_xact_lock_user", None)
    if advisory_lock is not None:
        await advisory_lock(session, message.from_user.id)

    topic = await repository.get_by_user_id(session, message.from_user.id)
    if topic is not None:
        return topic

    try:
        forum_topic = await bot.create_forum_topic(
            chat_id=support_chat_id,
            name=_topic_name(message),
        )
    except TelegramBadRequest as exc:
        if "not enough rights" in str(exc).lower():
            raise SupportChatConfigError(
                "Bot cannot create forum topics. Grant the bot admin permission "
                "'Manage Topics' in the support supergroup."
            ) from exc
        raise SupportChatConfigError(
            "Cannot create Telegram forum topic. Check SUPPORT_CHAT_ID: "
            "it must be the id of a supergroup with topics enabled, "
            "usually in -100... format."
        ) from exc
    return await _call_create_support_topic(
        repository,
        session=session,
        user_id=message.from_user.id,
        topic_id=forum_topic.message_thread_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )


async def get_by_topic_id(session: object, topic_id: int) -> Optional[object]:
    return await _repositories().get_by_topic_id(session, topic_id)


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


def _topic_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return f"user:{message.chat.id}"

    name = user.full_name or user.username or str(user.id)
    return f"{name} ({user.id})"[:128]


def _repositories() -> object:
    return import_module("database.repositories")


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
