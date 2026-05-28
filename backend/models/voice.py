from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


class VoiceNote(Base, IdMixin, TimestampMixin):
    """录音文件 + ASR 转写结果"""

    __tablename__ = "voice_notes"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mdt_sessions.id"), nullable=False, index=True
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # patient_request | mdt_discussion
    voice_type: Mapped[str] = mapped_column(String(32), default="patient_request")
    duration: Mapped[Optional[float]] = mapped_column(Float)
    # 分片合并状态
    chunk_count: Mapped[int] = mapped_column(Integer, default=1)
    # pending / processing / done / failed
    asr_status: Mapped[str] = mapped_column(String(32), default="pending")
    # 转写结果: [{"speaker": "SP01", "start": 0.0, "end": 12.3, "text": "...", "dept": "外科"}]
    transcript: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    error: Mapped[Optional[str]] = mapped_column(Text)
    # 浏览器 MediaRecorder.mimeType,finalize 阶段 ffmpeg 据此选容器格式
    source_mime: Mapped[Optional[str]] = mapped_column(String(64))
