"""各科室意见 schema"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.asr import DepartmentName

EvidenceSource = Literal["录音", "病历", "医生补充"]
ConfidenceLevel = Literal["high", "medium", "low"]

# 必须覆盖的核心 6 科
CORE_DEPARTMENTS: tuple[str, ...] = (
    "外科",
    "肿瘤内科",
    "放射科",
    "放疗科",
    "介入治疗",
    "病理科",
)


class DepartmentOpinionSchema(BaseModel):
    """单个科室的 MDT 意见。is_missing=true 表示录音中未明确记录"""

    model_config = ConfigDict(extra="forbid")

    department: DepartmentName
    doctor_label: Optional[str] = Field(None, description="录音中的匿名 speaker_id 如 SP01")
    is_missing: bool = Field(
        default=False,
        description="是否本次讨论中未明确记录该科室意见",
    )
    opinion: Optional[str] = Field(None, description="核心观点")
    rationale: Optional[str] = Field(None, description="理由依据")
    recommendation: Optional[str] = Field(None, description="具体建议")
    evidence_source: Optional[EvidenceSource] = None
    evidence_snippet: Optional[str] = Field(
        None, max_length=200, description="录音/病历原文片段"
    )
    confidence: ConfidenceLevel = "low"


class OpinionsExtractionResult(BaseModel):
    """LLM 提取各科室意见的总输出"""

    model_config = ConfigDict(extra="forbid")

    opinions: List[DepartmentOpinionSchema]
    consensus: Optional[str] = Field(None, description="共识点")
    disputes: Optional[str] = Field(None, description="分歧点")
