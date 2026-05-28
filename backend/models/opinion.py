from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class DepartmentOpinion(Base, IdMixin, TimestampMixin):
    """某科室在 MDT 中的意见。is_missing=true 表示未明确记录"""

    __tablename__ = "department_opinions"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    # 外科 / 肿瘤内科 / 放射 / 放疗 / 介入 / 病理 / 核医学 / 营养 / 其他
    department: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    doctor_label: Mapped[Optional[str]] = mapped_column(String(32))  # SP01 etc
    opinion: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    recommendation: Mapped[Optional[str]] = mapped_column(Text)
    # 录音 / 病历 / 医生补充
    evidence_source: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_snippet: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
