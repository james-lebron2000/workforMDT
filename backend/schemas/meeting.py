"""MDT 群组会议:候选患者元数据 + LLM 切分输出 schema"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.asr import TranscriptSegment


class MeetingCandidate(BaseModel):
    """切分时给 LLM 的候选患者元数据。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    patient_code: str
    primary_diagnosis: Optional[str] = None
    primary_site: Optional[str] = None


class MeetingSplit(BaseModel):
    """LLM 给一个候选患者的切分结果。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str  # 候选 session_id 或字面值 "__unassigned__"
    patient_code: str
    is_missing: bool = Field(
        default=False, description="该患者完全未被讨论"
    )
    segments: List[TranscriptSegment] = Field(
        default_factory=list, description="归到该患者的原 segments(完全照搬)"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: Optional[str] = Field(
        None, max_length=300, description="最能体现归属此患者的过渡语原文"
    )


class MeetingSplitResult(BaseModel):
    """切分总输出。"""

    model_config = ConfigDict(extra="forbid")

    splits: List[MeetingSplit]
