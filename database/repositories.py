from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SupportTopic


def _build_full_name(
    full_name: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> Optional[str]:
    if full_name:
        return full_name

    name_parts = []
    if first_name:
        name_parts.append(first_name)
    if last_name:
        name_parts.append(last_name)

    if not name_parts:
        return None
    return " ".join(name_parts)


async def advisory_xact_lock_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:user_id)"),
        {"user_id": user_id},
    )


async def get_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> Optional[SupportTopic]:
    result = await session.execute(
        select(SupportTopic).where(SupportTopic.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_by_topic_id(
    session: AsyncSession,
    topic_id: int,
) -> Optional[SupportTopic]:
    result = await session.execute(
        select(SupportTopic).where(SupportTopic.topic_id == topic_id)
    )
    return result.scalar_one_or_none()


async def create_support_topic(
    session: AsyncSession,
    *,
    user_id: int,
    topic_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    status: str = "open",
) -> SupportTopic:
    topic = SupportTopic(
        user_id=user_id,
        topic_id=topic_id,
        username=username,
        full_name=_build_full_name(full_name, first_name, last_name),
        status=status,
    )
    session.add(topic)
    await session.flush()
    await session.refresh(topic)
    return topic


async def get_or_create_support_topic(
    session: AsyncSession,
    *,
    user_id: int,
    topic_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    status: str = "open",
) -> Tuple[SupportTopic, bool]:
    await advisory_xact_lock_user(session, user_id)

    topic = await get_by_user_id(session, user_id)
    if topic is not None:
        return topic, False

    topic = await create_support_topic(
        session,
        user_id=user_id,
        topic_id=topic_id,
        username=username,
        full_name=full_name,
        first_name=first_name,
        last_name=last_name,
        status=status,
    )
    return topic, True


async def update_support_topic(
    session: AsyncSession,
    topic: SupportTopic,
    *,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    status: Optional[str] = None,
) -> SupportTopic:
    if username is not None:
        topic.username = username
    if full_name is not None:
        topic.full_name = full_name
    if status is not None:
        topic.status = status

    await session.flush()
    await session.refresh(topic)
    return topic


async def set_support_topic_status(
    session: AsyncSession,
    *,
    user_id: int,
    status: str,
) -> Optional[SupportTopic]:
    topic = await get_by_user_id(session, user_id)
    if topic is None:
        return None
    return await update_support_topic(session, topic, status=status)
