from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib import import_module
from inspect import Parameter, isawaitable, signature
from typing import Optional

from aiogram import Bot
from aiogram.types import Message

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

    forum_topic = await bot.create_forum_topic(
        chat_id=support_chat_id,
        name=_topic_name(message),
    )
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
