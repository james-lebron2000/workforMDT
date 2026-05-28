"""通用 schema 字段"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceMixin(BaseModel):
    """每个 LLM 输出字段必带的证据引用。"""

    model_config = ConfigDict(extra="forbid")

    evidence_snippet: Optional[str] = Field(
        default=None,
        max_length=200,
        description="原文证据片段(≤80 字典型)。找不到则填 null,不要编造。",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="对该字段判断的置信度 0-1"
    )
