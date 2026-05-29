"""MDT 群组会议 — 一次实际的 MDT 会议里讨论 N 个病人。

设计:
- 一个 MdtMeeting 关联 N 个 MdtSession(多对多 mdt_meeting_sessions)。
- 一段整段录音(voice_notes.id 写在 MdtMeeting.group_voice_id)对应整个会议;
  后端 ASR 完成后由 agent_08_meeting_splitter 按语义切分成 N 段,把每段 transcript
  写回该 session 的 mdt_discussion VoiceNote(新建一条,asr_status='done',meeting_id 回填)。
- 之后每个 session 走现有 mdt_analysis_task 即可,无需改造。
- 状态机:
    draft         刚创建,未开始录音
    recording     录音中(group_voice_id 已生成)
    transcribing  ASR 处理整段
    splitting     语义切分给各 session
    analyzing     各 session 7-agent 串联中
    completed     全部 session 出报告
    failed        切分/合成阶段异常

红线松:此路径允许 session.status=draft/collecting(未确认病史)进入会议;
        会后医生需补做病史核对。前端必须显式提示。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import Column, Date, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models._common import IdMixin, TimestampMixin


# 多对多关联表 — 一次会议可有 N 个 session
mdt_meeting_sessions = Table(
    "mdt_meeting_sessions",
    Base.metadata,
    Column(
        "meeting_id",
        String(36),
        ForeignKey("mdt_meetings.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "session_id",
        String(36),
        ForeignKey("mdt_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class MdtMeeting(Base, IdMixin, TimestampMixin):
    """一次实际 MDT 会议(可能讨论多个患者)。"""

    __tablename__ = "mdt_meetings"

    title: Mapped[Optional[str]] = mapped_column(String(256))
    mdt_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    # 整段群组录音的 voice_note.id(创建会议时由后端预生成 → 给 Recorder 用)
    # 不加 FK 约束以避免迁移先后顺序问题;实际有效性由代码保证。
    group_voice_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    # LLM 切分摘要(调试 + 审计用):
    # [{"session_id": "...", "patient_code": "P-001", "segments": [...],
    #   "confidence": 0.85, "evidence": "...", "is_missing": false}]
    split_summary: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    # 失败/部分失败时给前端显示的中文消息
    error: Mapped[Optional[str]] = mapped_column(Text)
