from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class CaseSummary(Base, IdMixin, TimestampMixin):
    """MDT 前生成的病例摘要 - 用于给 MDT 医生看"""

    __tablename__ = "case_summaries"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    chief_need: Mapped[Optional[str]] = mapped_column(Text)
    history_summary: Mapped[Optional[str]] = mapped_column(Text)
    # [{"date": "2021-03", "event": "确诊", "evidence_snippet": "..."}]
    treatment_timeline: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    current_problem: Mapped[Optional[str]] = mapped_column(Text)
    # ["是否能手术?", "下一步化疗方案?"]
    mdt_questions: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
