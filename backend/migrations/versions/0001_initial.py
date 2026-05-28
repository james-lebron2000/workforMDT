"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("openid", sa.String(64), unique=True),
        sa.Column("device_id", sa.String(64)),
        sa.Column("name", sa.String(64)),
        sa.Column("hospital", sa.String(128)),
        sa.Column("dept", sa.String(64)),
        sa.Column("role", sa.String(32), nullable=False, server_default="doctor"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_openid", "users", ["openid"])
    op.create_index("ix_users_device_id", "users", ["device_id"])

    op.create_table(
        "patients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("sex", sa.String(8)),
        sa.Column("age_range", sa.String(16)),
        sa.Column("primary_diagnosis", sa.String(256)),
        sa.Column("primary_site", sa.String(128)),
        sa.Column("current_status", sa.Text),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_patients_code", "patients", ["code"])

    op.create_table(
        "mdt_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("patient_id", sa.String(36), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("mdt_date", sa.Date),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mdt_sessions_patient_id", "mdt_sessions", ["patient_id"])

    op.create_table(
        "medical_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("file_key", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(64), server_default="other"),
        sa.Column("mime_type", sa.String(64)),
        sa.Column("ocr_status", sa.String(32), server_default="pending"),
        sa.Column("raw_text_key", sa.String(512)),
        sa.Column("structured", postgresql.JSONB),
        sa.Column("confidence", sa.Float),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_medical_records_session_id", "medical_records", ["session_id"])

    op.create_table(
        "voice_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("file_key", sa.String(512), nullable=False),
        sa.Column("voice_type", sa.String(32), server_default="patient_request"),
        sa.Column("duration", sa.Float),
        sa.Column("chunk_count", sa.Integer, server_default="1"),
        sa.Column("asr_status", sa.String(32), server_default="pending"),
        sa.Column("transcript", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_voice_notes_session_id", "voice_notes", ["session_id"])

    op.create_table(
        "case_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("chief_need", sa.Text),
        sa.Column("history_summary", sa.Text),
        sa.Column("treatment_timeline", postgresql.JSONB),
        sa.Column("current_problem", sa.Text),
        sa.Column("mdt_questions", postgresql.JSONB),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_case_summaries_session_id", "case_summaries", ["session_id"])

    op.create_table(
        "tnm_stagings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("tnm_type", sa.String(8), nullable=False),
        sa.Column("t_stage", sa.String(16), nullable=False),
        sa.Column("n_stage", sa.String(16), nullable=False),
        sa.Column("m_stage", sa.String(16), nullable=False),
        sa.Column("overall_stage", sa.String(16), nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.Column("uncertainty", sa.Text),
        sa.Column("confidence", sa.Float, server_default="0.0"),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tnm_stagings_session_id", "tnm_stagings", ["session_id"])

    op.create_table(
        "department_opinions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("department", sa.String(32), nullable=False),
        sa.Column("doctor_label", sa.String(32)),
        sa.Column("opinion", sa.Text),
        sa.Column("rationale", sa.Text),
        sa.Column("recommendation", sa.Text),
        sa.Column("evidence_source", sa.String(32)),
        sa.Column("evidence_snippet", sa.Text),
        sa.Column("confidence", sa.Float, server_default="0.0"),
        sa.Column("is_missing", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_department_opinions_session_id", "department_opinions", ["session_id"])
    op.create_index("ix_department_opinions_department", "department_opinions", ["department"])

    op.create_table(
        "final_recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("clinical_judgment", sa.Text),
        sa.Column("exam_recommendations", postgresql.JSONB),
        sa.Column("treatment_recommendations", postgresql.JSONB),
        sa.Column("referral", postgresql.JSONB),
        sa.Column("patient_script", sa.Text),
        sa.Column("qc_status", sa.String(16), server_default="pending"),
        sa.Column("qc_issues", postgresql.JSONB),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_final_recommendations_session_id", "final_recommendations", ["session_id"])

    op.create_table(
        "field_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mdt_sessions.id"), nullable=False),
        sa.Column("field_path", sa.String(256), nullable=False),
        sa.Column("before", sa.Text),
        sa.Column("after", sa.Text),
        sa.Column("doctor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_field_revisions_session_id", "field_revisions", ["session_id"])

    op.create_table(
        "user_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", sa.String(36)),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("ip", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    for tbl in [
        "audit_logs",
        "user_consents",
        "field_revisions",
        "final_recommendations",
        "department_opinions",
        "tnm_stagings",
        "case_summaries",
        "voice_notes",
        "medical_records",
        "mdt_sessions",
        "patients",
        "users",
    ]:
        op.drop_table(tbl)
