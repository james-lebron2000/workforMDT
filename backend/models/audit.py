from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class AuditLog(Base, IdMixin, TimestampMixin):
    """所有数据访问/修改的审计日志"""

    __tablename__ = "audit_logs"

    actor_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(64))
    target_id: Mapped[Optional[str]] = mapped_column(String(36))
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
