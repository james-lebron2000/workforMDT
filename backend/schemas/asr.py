"""ASR 阶段 - 自部署 ASR 服务的接口契约 + 说话人/科室归类"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DepartmentName = Literal[
    "外科",
    "肿瘤内科",
    "放射科",
    "放疗科",
    "介入治疗",
    "病理科",
    "核医学",
    "营养支持",
    "姑息治疗",
    "其他",
    "未知",
]


class TranscriptSegment(BaseModel):
    """ASR 一条片段(声学分组,未归科室)"""

    model_config = ConfigDict(extra="ignore")

    speaker: str  # SP01 / SP02 / ...
    start: float
    end: float
    text: str


class AsrRawResponse(BaseModel):
    """自部署 ASR 服务返回的原始结构"""

    model_config = ConfigDict(extra="ignore")

    text: str  # 整段拼接
    segments: List[TranscriptSegment]
    speakers: List[str]  # 出现过的 speaker_id
    duration: float = 0.0


# ---------- LLM 归类输出 ----------


class SpeakerClassification(BaseModel):
    """LLM 把每个 speaker_id 归到某个科室"""

    model_config = ConfigDict(extra="forbid")

    speaker: str
    department: DepartmentName
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: Optional[str] = Field(None, max_length=200, description="判断依据片段")


class SpeakerClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: List[SpeakerClassification]


class ClassifiedSegment(TranscriptSegment):
    """归类后的转写片段"""

    department: DepartmentName = "未知"
