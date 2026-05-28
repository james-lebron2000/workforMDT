"""TNM 分期 schema - 关键防幻觉,枚举强约束 + 必给依据"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TnmType = Literal["cTNM", "pTNM", "ycTNM", "rTNM"]

# AJCC 8th 通用枚举(覆盖大部分实体瘤的可能取值)
TStage = Literal[
    "Tx", "T0", "Tis",
    "T1", "T1a", "T1b", "T1c",
    "T2", "T2a", "T2b",
    "T3", "T3a", "T3b",
    "T4", "T4a", "T4b", "T4c", "T4d",
]
NStage = Literal[
    "Nx", "N0",
    "N1", "N1a", "N1b", "N1c", "N1mi",
    "N2", "N2a", "N2b", "N2c",
    "N3", "N3a", "N3b", "N3c",
]
MStage = Literal[
    "M0",
    "M1", "M1a", "M1b", "M1c", "M1d",
]
OverallStage = Literal[
    "0",
    "I", "IA", "IB",
    "II", "IIA", "IIB", "IIC",
    "III", "IIIA", "IIIB", "IIIC",
    "IV", "IVA", "IVB", "IVC",
    "unknown",
]


class TnmStagingSchema(BaseModel):
    """TNM 分期。每个字段必填或可解释。"""

    model_config = ConfigDict(extra="forbid")

    tnm_type: TnmType = Field(..., description="cTNM/pTNM/ycTNM/rTNM,根据数据来源判断")
    t_stage: TStage
    n_stage: NStage
    m_stage: MStage
    overall_stage: OverallStage
    basis: str = Field(
        ..., min_length=10,
        description="判定依据,必须引用病理/影像/手术记录中的具体文字。少于 10 字不通过。"
    )
    uncertainty: Optional[str] = Field(
        None,
        description="不确定项 - 缺哪些资料、为何标 Tx/N? 等。若无不确定项填 null"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("basis")
    @classmethod
    def basis_not_empty_placeholder(cls, v: str) -> str:
        # 防止 LLM 偷懒填占位符
        v = v.strip()
        if v.lower() in {"n/a", "无", "unknown", "tbd", "待定", "未知"}:
            raise ValueError("basis 必须给出实际依据,不能用占位符")
        return v
