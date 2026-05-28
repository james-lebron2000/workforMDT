from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class UserConsent(Base, IdMixin, TimestampMixin):
    """同意书签署记录"""

    __tablename__ = "user_consents"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
