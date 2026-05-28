"""病例摘要 schema"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import EvidenceMixin


class TimelineEvent(EvidenceMixin):
    date: str = Field(..., description="ISO 格式或 YYYY-MM")
    event: str = Field(..., description="发生的事件,例:确诊直肠癌、行 R0 切除等")


class CaseSummarySchema(BaseModel):
    """MDT 前的病例摘要(对应数据库 case_summaries 表)"""

    model_config = ConfigDict(extra="forbid")

    chief_need: str = Field(..., description="本次就诊核心诉求与预期收获")
    history_summary: str = Field(..., max_length=2000, description="≤500 字病情综述")
    treatment_timeline: List[TimelineEvent]
    current_problem: str = Field(..., description="当前需 MDT 解决的核心问题")
    mdt_questions: List[str] = Field(..., min_length=1, description="需 MDT 讨论的具体问题列表")
