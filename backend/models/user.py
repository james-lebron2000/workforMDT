from __future__ import annotations

from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class User(Base, IdMixin, TimestampMixin):
    """医生用户。MVP 用微信 OpenID 登录;开发期允许匿名 device_id"""

    __tablename__ = "users"

    openid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(64))
    hospital: Mapped[Optional[str]] = mapped_column(String(128))
    dept: Mapped[Optional[str]] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32), default="doctor")
