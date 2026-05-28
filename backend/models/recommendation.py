from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class FinalRecommendation(Base, IdMixin, TimestampMixin):
    """最终综合建议(覆盖所有 6 字段) - MDT 报告的核心"""

    __tablename__ = "final_recommendations"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    clinical_judgment: Mapped[Optional[str]] = mapped_column(Text)
    # [{"name": "增强 CT", "reason": "...", "priority": "必查"}]
    exam_recommendations: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    # [{"line": 1, "regimen": "FOLFOX", "evidence_level": "I", "rationale": "..."}]
    treatment_recommendations: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    # [{"dept": "结直肠肿瘤内科", "doctor": "XX教授", "reason": "...", "priority": "高"}]
    referral: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    patient_script: Mapped[Optional[str]] = mapped_column(Text)
    # QC 检查结果: passed / warning / failed
    qc_status: Mapped[str] = mapped_column(String(16), default="pending")
    qc_issues: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
