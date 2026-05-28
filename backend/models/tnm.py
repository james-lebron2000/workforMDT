from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class TnmStaging(Base, IdMixin, TimestampMixin):
    """TNM 分期。type ∈ cTNM/pTNM/ycTNM/rTNM"""

    __tablename__ = "tnm_stagings"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    tnm_type: Mapped[str] = mapped_column(String(8), nullable=False)
    t_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    n_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    m_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    overall_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1)
