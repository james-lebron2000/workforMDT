"""voice_notes: add source_mime (MediaRecorder.mimeType for ffmpeg transcode)

Revision ID: 0002_voice_source_mime
Revises: 0001_initial
Create Date: 2026-05-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_voice_source_mime"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_notes",
        sa.Column("source_mime", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voice_notes", "source_mime")
