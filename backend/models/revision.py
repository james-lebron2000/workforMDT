from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class FieldRevision(Base, IdMixin, TimestampMixin):
    """医生对某个字段的修改记录 - 完整审计"""

    __tablename__ = "field_revisions"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    # 例: tnm.t_stage / department_opinions.外科.opinion
    field_path: Mapped[str] = mapped_column(String(256), nullable=False)
    before: Mapped[Optional[str]] = mapped_column(Text)
    after: Mapped[Optional[str]] = mapped_column(Text)
    doctor_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
