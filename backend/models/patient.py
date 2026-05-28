from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class Patient(Base, IdMixin, TimestampMixin):
    """患者代号 - 仅存匿名代号/化名,不存真名/身份证/手机"""

    __tablename__ = "patients"

    code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    sex: Mapped[Optional[str]] = mapped_column(String(8))
    age_range: Mapped[Optional[str]] = mapped_column(String(16))  # "50-60"
    primary_diagnosis: Mapped[Optional[str]] = mapped_column(String(256))
    primary_site: Mapped[Optional[str]] = mapped_column(String(128))
    current_status: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
