"""最终 MDT 报告 schema - 6 字段综合输出"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.asr import DepartmentName
from schemas.opinion import DepartmentOpinionSchema
from schemas.summary import CaseSummarySchema
from schemas.tnm import TnmStagingSchema

# ---------- 检查建议 ----------

ExamPriority = Literal["必查", "建议", "可选"]
ExamCategory = Literal[
    "影像检查",
    "病理检查",
    "分子检测",
    "实验室检查",
    "功能状态评估",
    "营养评估",
    "治疗前安全性评估",
    "其他",
]


class ExamRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ExamCategory
    name: str
    reason: str = Field(..., min_length=4)
    priority: ExamPriority = "建议"


# ---------- 治疗建议 ----------

TreatmentKind = Literal[
    "首选治疗",
    "备选治疗",
    "不推荐治疗",
    "转化治疗",
    "姑息治疗",
    "临床试验",
    "随访计划",
]
EvidenceLevel = Literal["I", "II", "III", "IV", "未分级"]


class TreatmentRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TreatmentKind
    regimen: str = Field(..., description="具体方案,如 'FOLFOX + 贝伐珠单抗'")
    rationale: str = Field(..., min_length=4)
    evidence_level: EvidenceLevel = "未分级"
    needs_doctor_confirm: bool = Field(
        default=True, description="是否需医生最终确认 - 应永远为 True"
    )

    @field_validator("needs_doctor_confirm")
    @classmethod
    def must_need_confirm(cls, v: bool) -> bool:
        # 硬约束:此字段不允许被 LLM 设为 False
        return True


# ---------- 推荐医生 ----------

ReferralPriority = Literal["高", "中", "低"]


class ReferralRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dept: str = Field(..., description="推荐就诊科室/门诊")
    doctor_hint: Optional[str] = Field(
        None, description="推荐医生类型(教授级/亚专科/MDT 协调人等),不写具体人名"
    )
    reason: str = Field(..., min_length=4)
    priority: ReferralPriority = "中"
    bring_with: List[str] = Field(default_factory=list, description="需携带的资料")


# ---------- QC ----------

QCSeverity = Literal["info", "warning", "critical"]


class QCIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    severity: QCSeverity
    issue: str
    suggestion: Optional[str] = None


class QCReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: List[QCIssue] = []
    must_fix: List[str] = []


# ---------- 综合最终建议 ----------


class FinalRecommendationSchema(BaseModel):
    """MDT 最终综合建议 - 6 个字段"""

    model_config = ConfigDict(extra="forbid")

    clinical_judgment: str = Field(..., min_length=10, description="临床判断综述")
    tnm: TnmStagingSchema
    suggested_exams: List[ExamRecommendation]
    treatment_plan: List[TreatmentRecommendation]
    referral: List[ReferralRecommendation]
    patient_script: str = Field(
        ...,
        min_length=20,
        max_length=1500,
        description="给患者/家属的反馈话术 - 通俗、谨慎、不承诺疗效",
    )

    @field_validator("patient_script")
    @classmethod
    def no_overcommit(cls, v: str) -> str:
        forbidden = ["治愈", "一定能", "保证", "百分百", "肯定治好", "包治"]
        for word in forbidden:
            if word in v:
                raise ValueError(
                    f"患者话术不得含承诺词 '{word}'。请使用'有助于'/'可能'/'通常'等措辞"
                )
        return v


class MdtFullReport(BaseModel):
    """完整 MDT 报告的聚合 schema(数据库各表反序列化用)"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    case_summary: CaseSummarySchema
    opinions: List[DepartmentOpinionSchema]
    final: FinalRecommendationSchema
    qc: QCReport
