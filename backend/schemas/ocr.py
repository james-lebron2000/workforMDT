"""OCR 阶段 - 自部署 OCR 服务的接口契约 + LLM 抽取的结构化输出"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.common import EvidenceMixin


class OcrBlock(BaseModel):
    """OCR 一个文字块"""

    model_config = ConfigDict(extra="ignore")

    text: str
    bbox: Optional[List[float]] = None  # [x1,y1,x2,y2]
    confidence: float = 0.0


class OcrTableCell(BaseModel):
    model_config = ConfigDict(extra="ignore")

    row: int
    col: int
    text: str


class OcrTable(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cells: List[OcrTableCell] = []
    row_count: int = 0
    col_count: int = 0


class OcrRawResponse(BaseModel):
    """自部署 OCR 服务返回的原始结构"""

    model_config = ConfigDict(extra="ignore")

    raw_text: str
    blocks: List[OcrBlock] = []
    tables: List[OcrTable] = []
    confidence: float = 0.0


# ---------- LLM 抽取结果 ----------


FileType = Literal[
    "outpatient_record",   # 门诊病历
    "discharge_summary",   # 出院小结
    "pathology",           # 病理报告
    "imaging",             # 影像报告
    "lab",                 # 化验单
    "genetic",             # 基因检测
    "chemotherapy",        # 化疗记录
    "surgery",             # 手术记录
    "mdt_record",          # 既往 MDT 记录
    "patient_question",    # 患者手写问题
    "other",
]


class LabValue(EvidenceMixin):
    name: str
    value: str
    unit: Optional[str] = None
    date: Optional[str] = None  # ISO


class ImagingFinding(EvidenceMixin):
    modality: Optional[str] = None  # CT/MRI/PET 等
    finding: str
    location: Optional[str] = None
    date: Optional[str] = None


class TreatmentEvent(EvidenceMixin):
    date: Optional[str] = None
    type: str  # 手术/化疗/放疗/靶向/免疫/介入
    detail: str


class OcrExtraction(BaseModel):
    """LLM 把 OCR rawText 抽出来的结构化"""

    model_config = ConfigDict(extra="forbid")

    file_type: FileType
    diagnosis: Optional[str] = Field(None, description="主要诊断")
    pathology_type: Optional[str] = None
    differentiation: Optional[str] = None
    primary_site: Optional[str] = None
    stage: Optional[str] = Field(None, description="既往分期,若文档已提供")
    mmr_msi: Optional[str] = None
    ras_braf_her2: Optional[Dict[str, Any]] = Field(default_factory=dict)
    lab_values: List[LabValue] = []
    imaging_findings: List[ImagingFinding] = []
    treatment_events: List[TreatmentEvent] = []
    notes: Optional[str] = None


class OcrVisionResult(BaseModel):
    """视觉 LLM 一次性输出 — raw_text + 结构化 + 整体置信度。

    用于替代「自部署 PaddleOCR → 文本 LLM 抽取」两段式;
    直接把图片送给多模态 LLM(豆包 Seed 1.6),同步完成转写和抽取,
    省去一个 GPU 自部署依赖。
    """

    model_config = ConfigDict(extra="ignore")

    raw_text: str = Field(..., description="按阅读顺序逐字转写;多页用 ---page-break--- 分隔")
    extraction: OcrExtraction
    confidence: float = Field(0.0, ge=0.0, le=1.0)
