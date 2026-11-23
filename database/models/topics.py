from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.config import Base


class SupportTopic(Base):
    __tablename__ = "support_topics"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_support_topics_user_id"),
        UniqueConstraint("topic_id", name="uq_support_topics_topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    is_banned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    ban_reason: Mapped[Optional[str]] = mapped_column(String(512))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    header_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index(
            "uq_support_tickets_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status != 'closed'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("support_topics.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    assigned_operator_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    source_payload: Mapped[Optional[str]] = mapped_column(String(255))
    tags: Mapped[Optional[str]] = mapped_column(String(512))
    csat_score: Mapped[Optional[int]] = mapped_column(SmallInteger)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_user_msg_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_operator_msg_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[Optional[str]] = mapped_column(String(64))


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(255))
    new_value: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CannedResponse(Base):
    __tablename__ = "canned_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
