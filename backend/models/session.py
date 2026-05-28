from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class MdtSession(Base, IdMixin, TimestampMixin):
    """一次 MDT 会议的主聚合根"""

    __tablename__ = "mdt_sessions"

    patient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("patients.id"), nullable=False, index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(256))
    mdt_date: Mapped[Optional[date]] = mapped_column(Date)
    # 状态机:
    #   draft              新建,未上传任何资料
    #   collecting         OCR 资料中(已有 ≥1 份资料)
    #   summary_confirmed  病史摘要已与患者核对确认,可进入 MDT 录音
    #   recording          MDT 录音中(已有 mdt_discussion voice note)
    #   analyzing          AI 综合分析中
    #   reviewing          待医生确认报告
    #   completed          报告已确认完成
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
