from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CannedResponse, SupportTicket, SupportTopic, TicketEvent

_UNSET: Any = object()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_full_name(
    full_name: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> Optional[str]:
    if full_name:
        return full_name
    parts = [p for p in (first_name, last_name) if p]
    return " ".join(parts) if parts else None


async def advisory_xact_lock_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:user_id)"),
        {"user_id": user_id},
    )


async def get_by_user_id(session: AsyncSession, user_id: int) -> Optional[SupportTopic]:
    result = await session.execute(
        select(SupportTopic).where(SupportTopic.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_by_topic_id(session: AsyncSession, topic_id: int) -> Optional[SupportTopic]:
    result = await session.execute(
        select(SupportTopic).where(SupportTopic.topic_id == topic_id)
    )
    return result.scalar_one_or_none()


async def get_open_topics(session: AsyncSession, *, lock: bool = False) -> Sequence[SupportTopic]:
    stmt = select(SupportTopic).where(SupportTopic.status == "open")
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    result = await session.execute(stmt)
    return result.scalars().all()


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
    header_message_id: Optional[int] = None,
) -> SupportTopic:
    topic = SupportTopic(
        user_id=user_id,
        topic_id=topic_id,
        username=username,
        full_name=_build_full_name(full_name, first_name, last_name),
        status=status,
        header_message_id=header_message_id,
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
    username: Any = _UNSET,
    full_name: Any = _UNSET,
    status: Any = _UNSET,
    header_message_id: Any = _UNSET,
    is_banned: Any = _UNSET,
    ban_reason: Any = _UNSET,
    notes: Any = _UNSET,
) -> SupportTopic:
    if username is not _UNSET:
        topic.username = username
    if full_name is not _UNSET:
        topic.full_name = full_name
    if status is not _UNSET and status is not None:
        topic.status = status
    if header_message_id is not _UNSET:
        topic.header_message_id = header_message_id
    if is_banned is not _UNSET:
        topic.is_banned = is_banned
    if ban_reason is not _UNSET:
        topic.ban_reason = ban_reason
    if notes is not _UNSET:
        topic.notes = notes

    await session.flush()
    await session.refresh(topic)
    return topic


async def update_topic_id(
    session: AsyncSession,
    topic: SupportTopic,
    topic_id: int,
) -> SupportTopic:
    topic.topic_id = topic_id
    topic.status = "open"
    await session.flush()
    await session.refresh(topic)
    return topic


async def touch_support_topic(
    session: AsyncSession,
    topic: SupportTopic,
    username: Any = _UNSET,
    full_name: Any = _UNSET,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    status: Optional[str] = None,
) -> SupportTopic:
    if username is not _UNSET:
        topic.username = username

    if full_name is not _UNSET:
        built_full_name = _build_full_name(full_name, first_name, last_name)
        topic.full_name = built_full_name

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


# --- Ticket & Event CRM Operations ---


async def get_active_ticket_by_user(
    session: AsyncSession,
    user_id: int,
) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id, SupportTicket.status != "closed")
        .order_by(SupportTicket.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_ticket_by_id(session: AsyncSession, ticket_id: int) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    return result.scalar_one_or_none()


async def get_latest_ticket_by_user(
    session: AsyncSession,
    user_id: int,
) -> Optional[SupportTicket]:
    result = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def count_user_tickets(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user_id)
    )
    return int(result.scalar_one() or 0)


async def create_ticket(
    session: AsyncSession,
    *,
    user_id: int,
    topic_id: int,
    source_payload: Optional[str] = None,
    status: str = "new",
) -> SupportTicket:
    ticket = SupportTicket(
        user_id=user_id,
        topic_id=topic_id,
        status=status,
        source_payload=source_payload,
        last_user_msg_at=_utcnow(),
    )
    session.add(ticket)
    await session.flush()
    await session.refresh(ticket)
    return ticket


async def record_ticket_event(
    session: AsyncSession,
    *,
    ticket_id: int,
    actor_type: str,
    event_type: str,
    actor_id: Optional[int] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
) -> TicketEvent:
    event = TicketEvent(
        ticket_id=ticket_id,
        actor_id=actor_id,
        actor_type=actor_type,
        event_type=event_type,
        old_value=old_value,
        new_value=new_value,
    )
    session.add(event)
    await session.flush()
    return event


async def update_ticket(
    session: AsyncSession,
    ticket: SupportTicket,
    **fields: Any,
) -> SupportTicket:
    for key, value in fields.items():
        setattr(ticket, key, value)
    await session.flush()
    await session.refresh(ticket)
    return ticket


async def close_active_ticket(
    session: AsyncSession,
    *,
    user_id: int,
    closed_by: str,
    actor_type: str,
    actor_id: Optional[int] = None,
) -> Optional[SupportTicket]:
    ticket = await get_active_ticket_by_user(session, user_id)
    if ticket is None:
        return None
    old_status = ticket.status
    ticket.status = "closed"
    ticket.closed_at = _utcnow()
    ticket.closed_by = closed_by
    await session.flush()
    await record_ticket_event(
        session,
        ticket_id=ticket.id,
        actor_type=actor_type,
        actor_id=actor_id,
        event_type="status_changed",
        old_value=old_status,
        new_value="closed",
    )
    await session.refresh(ticket)
    return ticket


async def get_crm_stats(session: AsyncSession) -> Dict[str, Any]:
    status_rows = (
        await session.execute(
            select(SupportTicket.status, func.count(SupportTicket.id)).group_by(
                SupportTicket.status
            )
        )
    ).all()
    by_status = {status: int(cnt) for status, cnt in status_rows}

    avg_csat = (
        await session.execute(
            select(func.avg(SupportTicket.csat_score)).where(
                SupportTicket.csat_score.is_not(None)
            )
        )
    ).scalar_one_or_none()

    avg_frt_sec = (
        await session.execute(
            select(
                func.avg(
                    func.extract(
                        "epoch",
                        SupportTicket.first_response_at - SupportTicket.opened_at,
                    )
                )
            ).where(SupportTicket.first_response_at.is_not(None))
        )
    ).scalar_one_or_none()

    banned_count = (
        await session.execute(
            select(func.count(SupportTopic.id)).where(SupportTopic.is_banned.is_(True))
        )
    ).scalar_one()

    return {
        "by_status": by_status,
        "total_tickets": sum(by_status.values()),
        "avg_csat": round(float(avg_csat), 2) if avg_csat is not None else None,
        "avg_frt_sec": round(float(avg_frt_sec)) if avg_frt_sec is not None else None,
        "banned_users": int(banned_count or 0),
    }


# --- Canned Responses (Macros) ---


async def get_canned_response(
    session: AsyncSession,
    slug: str,
) -> Optional[CannedResponse]:
    result = await session.execute(
        select(CannedResponse).where(CannedResponse.slug == slug.lower())
    )
    return result.scalar_one_or_none()


async def list_canned_responses(session: AsyncSession) -> Sequence[CannedResponse]:
    result = await session.execute(
        select(CannedResponse).order_by(CannedResponse.slug.asc())
    )
    return result.scalars().all()


async def upsert_canned_response(
    session: AsyncSession,
    *,
    slug: str,
    content: str,
    created_by: Optional[int] = None,
) -> CannedResponse:
    clean_slug = slug.lower().strip()
    existing = await get_canned_response(session, clean_slug)
    if existing is not None:
        existing.content = content
        await session.flush()
        await session.refresh(existing)
        return existing
    macro = CannedResponse(slug=clean_slug, content=content, created_by=created_by)
    session.add(macro)
    await session.flush()
    await session.refresh(macro)
    return macro


async def delete_canned_response(session: AsyncSession, slug: str) -> bool:
    existing = await get_canned_response(session, slug)
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True
