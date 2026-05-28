"""病例 / MDT 会话相关请求-响应 schema"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- 请求 ----------


class CreateSessionRequest(BaseModel):
    patient_code: str = Field(..., min_length=1, max_length=32, description="患者代号(化名,不输真名)")
    patient_sex: Optional[Literal["男", "女", "其他", "未知"]] = None
    patient_age_range: Optional[str] = Field(None, description="如 '50-60'")
    primary_diagnosis: Optional[str] = Field(None, max_length=256)
    primary_site: Optional[str] = Field(None, max_length=128)
    title: Optional[str] = Field(None, max_length=256)
    mdt_date: Optional[date] = None


class UpdateSessionStatusRequest(BaseModel):
    status: Literal[
        "draft",
        "collecting",
        "recording",
        "analyzing",
        "reviewing",
        "completed",
    ]


# ---------- 响应 ----------


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str
    title: Optional[str]
    mdt_date: Optional[date]
    status: str
    created_at: datetime
    updated_at: datetime


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    sex: Optional[str]
    age_range: Optional[str]
    primary_diagnosis: Optional[str]
    primary_site: Optional[str]


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    patient: PatientResponse
    record_count: int = 0
    voice_count: int = 0
    has_summary: bool = False
    has_report: bool = False


class SessionListResponse(BaseModel):
    sessions: List[SessionDetailResponse]
    total: int
