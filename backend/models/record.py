from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class MedicalRecord(Base, IdMixin, TimestampMixin):
    """上传的病历资料(图片/PDF/Word) - rawText 写 MinIO,DB 只存结构化"""

    __tablename__ = "medical_records"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # outpatient_record / discharge_summary / pathology / imaging /
    # lab / genetic / chemotherapy / surgery / mdt_record / patient_question / other
    file_type: Mapped[str] = mapped_column(String(64), default="other")
    mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    # pending / processing / done / failed
    ocr_status: Mapped[str] = mapped_column(String(32), default="pending")
    raw_text_key: Mapped[Optional[str]] = mapped_column(String(512))
    structured: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    error: Mapped[Optional[str]] = mapped_column(Text)
