"""mdt_meetings + mdt_meeting_sessions + voice_notes.meeting_id

Revision ID: 0003_mdt_meetings
Revises: 0002_voice_source_mime
Create Date: 2026-05-29

引入"群组 MDT 会议"概念 — 一次实际 MDT 会议讨论多个患者,
录一段整段录音 + AI 切分回写到各 session。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_mdt_meetings"
down_revision = "0002_voice_source_mime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mdt_meetings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(256)),
        sa.Column("mdt_date", sa.Date),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("group_voice_id", sa.String(36)),
        sa.Column("split_summary", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_mdt_meetings_group_voice_id", "mdt_meetings", ["group_voice_id"])
    op.create_index("ix_mdt_meetings_created_by", "mdt_meetings", ["created_by"])

    op.create_table(
        "mdt_meeting_sessions",
        sa.Column(
            "meeting_id",
            sa.String(36),
            sa.ForeignKey("mdt_meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("mdt_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_mdt_meeting_sessions_session_id",
        "mdt_meeting_sessions",
        ["session_id"],
    )

    op.add_column(
        "voice_notes",
        sa.Column(
            "meeting_id",
            sa.String(36),
            sa.ForeignKey("mdt_meetings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_voice_notes_meeting_id", "voice_notes", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_notes_meeting_id", "voice_notes")
    op.drop_column("voice_notes", "meeting_id")

    op.drop_index("ix_mdt_meeting_sessions_session_id", "mdt_meeting_sessions")
    op.drop_table("mdt_meeting_sessions")

    op.drop_index("ix_mdt_meetings_created_by", "mdt_meetings")
    op.drop_index("ix_mdt_meetings_group_voice_id", "mdt_meetings")
    op.drop_table("mdt_meetings")
