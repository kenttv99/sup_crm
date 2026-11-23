"""add crm tickets events macros

Revision ID: 20261124_0002
Revises: 20260513_0001
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20261124_0002"
down_revision = "20260513_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "support_topics",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("support_topics", sa.Column("ban_reason", sa.String(length=512), nullable=True))
    op.add_column("support_topics", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("support_topics", sa.Column("header_message_id", sa.BigInteger(), nullable=True))

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("support_topics.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_operator_id", sa.BigInteger(), nullable=True),
        sa.Column("source_payload", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.String(length=512), nullable=True),
        sa.Column("csat_score", sa.SmallInteger(), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_user_msg_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_operator_msg_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_topic_id", "support_tickets", ["topic_id"])
    op.create_index(
        "ix_support_tickets_assigned_operator_id",
        "support_tickets",
        ["assigned_operator_id"],
    )
    op.create_index(
        "uq_support_tickets_active_user",
        "support_tickets",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status != 'closed'"),
    )

    op.create_table(
        "ticket_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ticket_id",
            sa.Integer(),
            sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ticket_events_ticket_id", "ticket_events", ["ticket_id"])

    op.create_table(
        "canned_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.execute(
        """
        INSERT INTO support_tickets (user_id, topic_id, status, opened_at, closed_at)
        SELECT
            user_id,
            topic_id,
            CASE WHEN status = 'closed' THEN 'closed' ELSE 'new' END,
            created_at,
            CASE WHEN status = 'closed' THEN updated_at ELSE NULL END
        FROM support_topics
        """
    )


def downgrade() -> None:
    op.drop_table("canned_responses")
    op.drop_index("ix_ticket_events_ticket_id", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("uq_support_tickets_active_user", table_name="support_tickets")
    op.drop_index("ix_support_tickets_assigned_operator_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_topic_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user_id", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_column("support_topics", "header_message_id")
    op.drop_column("support_topics", "notes")
    op.drop_column("support_topics", "ban_reason")
    op.drop_column("support_topics", "is_banned")
