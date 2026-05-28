"""MDT 会话 CRUD"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.audit import AuditLog
from models.opinion import DepartmentOpinion
from models.patient import Patient
from models.recommendation import FinalRecommendation
from models.record import MedicalRecord
from models.session import MdtSession
from models.summary import CaseSummary
from models.tnm import TnmStaging
from models.user import User
from models.voice import VoiceNote
from routers._deps import current_user
from routers.consent import require_consent
from services import minio_client
from services.sse_publisher import subscribe
from utils.logger import get_logger

logger = get_logger("router.sessions")

router = APIRouter()


class PatientPayload(BaseModel):
    code: str = Field(..., max_length=32, description="匿名代号")
    sex: Optional[str] = None
    age_range: Optional[str] = None
    primary_diagnosis: Optional[str] = None
    primary_site: Optional[str] = None


class SessionCreate(BaseModel):
    patient: PatientPayload
    title: Optional[str] = None
    mdt_date: Optional[date] = None


class SessionOut(BaseModel):
    id: str
    patient_id: str
    patient_code: str
    title: Optional[str]
    mdt_date: Optional[date]
    status: str

    model_config = {"from_attributes": True}


class SessionListOut(BaseModel):
    sessions: List[SessionOut]


@router.post("", response_model=SessionOut)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止创建会话
):
    patient = Patient(
        code=payload.patient.code,
        sex=payload.patient.sex,
        age_range=payload.patient.age_range,
        primary_diagnosis=payload.patient.primary_diagnosis,
        primary_site=payload.patient.primary_site,
        created_by=user.id,
    )
    db.add(patient)
    await db.flush()

    sess = MdtSession(
        patient_id=patient.id,
        title=payload.title or f"MDT-{payload.patient.code}",
        mdt_date=payload.mdt_date,
        status="draft",
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()

    db.add(AuditLog(
        actor_id=user.id, action="create_session",
        target_type="mdt_session", target_id=sess.id,
        payload={"patient_code": payload.patient.code},
    ))
    return SessionOut(
        id=sess.id,
        patient_id=patient.id,
        patient_code=patient.code,
        title=sess.title,
        mdt_date=sess.mdt_date,
        status=sess.status,
    )


@router.get("", response_model=SessionListOut)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    limit: int = 50,
):
    rows = (
        await db.execute(
            select(MdtSession, Patient)
            .join(Patient, MdtSession.patient_id == Patient.id)
            .where(MdtSession.created_by == user.id)
            .order_by(MdtSession.created_at.desc())
            .limit(limit)
        )
    ).all()
    return SessionListOut(
        sessions=[
            SessionOut(
                id=s.id,
                patient_id=p.id,
                patient_code=p.code,
                title=s.title,
                mdt_date=s.mdt_date,
                status=s.status,
            )
            for s, p in rows
        ]
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    patient = await db.get(Patient, sess.patient_id)
    records = list(
        (
            await db.execute(
                select(MedicalRecord).where(MedicalRecord.session_id == session_id)
            )
        ).scalars()
    )
    voices = list(
        (
            await db.execute(
                select(VoiceNote).where(VoiceNote.session_id == session_id)
            )
        ).scalars()
    )
    summary = (
        await db.execute(
            select(CaseSummary)
            .where(CaseSummary.session_id == session_id)
            .order_by(CaseSummary.version.desc())
        )
    ).scalar_one_or_none()
    tnm = (
        await db.execute(
            select(TnmStaging)
            .where(TnmStaging.session_id == session_id)
            .order_by(TnmStaging.version.desc())
        )
    ).scalar_one_or_none()
    opinions = list(
        (
            await db.execute(
                select(DepartmentOpinion).where(DepartmentOpinion.session_id == session_id)
            )
        ).scalars()
    )
    final = (
        await db.execute(
            select(FinalRecommendation)
            .where(FinalRecommendation.session_id == session_id)
            .order_by(FinalRecommendation.version.desc())
        )
    ).scalar_one_or_none()

    return {
        "session": {
            "id": sess.id,
            "title": sess.title,
            "mdt_date": sess.mdt_date,
            "status": sess.status,
            "created_at": sess.created_at,
            "patient": {
                "id": patient.id if patient else None,
                "code": patient.code if patient else None,
                "sex": patient.sex if patient else None,
                "age_range": patient.age_range if patient else None,
                "primary_diagnosis": patient.primary_diagnosis if patient else None,
                "primary_site": patient.primary_site if patient else None,
            },
        },
        "records": [
            {
                "id": r.id,
                "file_key": r.file_key,
                "file_type": r.file_type,
                "ocr_status": r.ocr_status,
                "structured": r.structured,
                "confidence": r.confidence,
            }
            for r in records
        ],
        "voices": [
            {
                "id": v.id,
                "file_key": v.file_key,
                "voice_type": v.voice_type,
                "asr_status": v.asr_status,
                "duration": v.duration,
                "transcript": v.transcript,
            }
            for v in voices
        ],
        "summary": summary and {
            "chief_need": summary.chief_need,
            "history_summary": summary.history_summary,
            "treatment_timeline": summary.treatment_timeline,
            "current_problem": summary.current_problem,
            "mdt_questions": summary.mdt_questions,
        },
        "tnm": tnm and {
            "tnm_type": tnm.tnm_type,
            "t_stage": tnm.t_stage,
            "n_stage": tnm.n_stage,
            "m_stage": tnm.m_stage,
            "overall_stage": tnm.overall_stage,
            "basis": tnm.basis,
            "uncertainty": tnm.uncertainty,
            "confidence": tnm.confidence,
        },
        "opinions": [
            {
                "id": o.id,
                "department": o.department,
                "doctor_label": o.doctor_label,
                "opinion": o.opinion,
                "rationale": o.rationale,
                "recommendation": o.recommendation,
                "evidence_source": o.evidence_source,
                "evidence_snippet": o.evidence_snippet,
                "confidence": o.confidence,
                "is_missing": o.is_missing,
            }
            for o in opinions
        ],
        "final": final and {
            "clinical_judgment": final.clinical_judgment,
            "exam_recommendations": final.exam_recommendations,
            "treatment_recommendations": final.treatment_recommendations,
            "referral": final.referral,
            "patient_script": final.patient_script,
            "qc_status": final.qc_status,
            "qc_issues": final.qc_issues,
        },
    }


class ConfirmSummaryRequest(BaseModel):
    note: Optional[str] = None  # 医生与患者核对的补充说明(可选)


@router.post("/{session_id}/confirm-summary")
async def confirm_summary(
    session_id: str,
    payload: ConfirmSummaryRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止确认摘要
):
    payload = payload or ConfirmSummaryRequest()
    """医生与患者核对完病史摘要后,锁定病史并解锁 MDT 录音环节。
    状态机:draft/collecting → summary_confirmed
    """
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    # 校验:必须已有一版 CaseSummary
    has_summary = (
        await db.execute(
            select(CaseSummary).where(CaseSummary.session_id == session_id)
        )
    ).first()
    if has_summary is None:
        raise HTTPException(
            status_code=400, detail="尚未生成病史摘要,请先点'生成病史摘要'"
        )

    sess.status = "summary_confirmed"
    db.add(AuditLog(
        actor_id=user.id, action="confirm_summary",
        target_type="mdt_session", target_id=session_id,
        payload={"note": payload.note} if payload.note else None,
    ))
    return {"ok": True, "status": sess.status}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """真删 - 级联删 MinIO 所有对象 + 所有相关行。

    红线:MinIO 删失败 → 不删 DB,返 500;否则会出现"DB 没行但 MinIO 残留原图/原录音"
    的孤儿数据,违反"用户点删就彻底删"承诺。
    """
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="not found")

    # 1. 先删 MinIO 对象;失败立即抛出,不进入 DB 删除阶段
    try:
        n = minio_client.remove_prefix(f"sessions/{session_id}/")
        logger.info("session_minio_removed", session_id=session_id, files=n)
    except RuntimeError as e:
        logger.error("session_delete_minio_failed", session_id=session_id, error=str(e))
        # 留一条 audit 记录失败的尝试,方便事后排查
        db.add(AuditLog(
            actor_id=user.id, action="delete_session_failed",
            target_type="mdt_session", target_id=session_id,
            payload={"reason": "minio_purge_failed", "detail": str(e)[:500]},
        ))
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail=(
                "对象存储清理失败,会话未删除以防孤儿数据。请稍后重试,"
                "若持续失败联系管理员检查 MinIO 状态。"
            ),
        ) from e

    # 2. MinIO 已干净,级联删 DB 行(ORM 简单删 — schema 已配 ondelete=CASCADE)
    for model_cls in [
        DepartmentOpinion,
        FinalRecommendation,
        TnmStaging,
        CaseSummary,
        MedicalRecord,
        VoiceNote,
    ]:
        rows = (
            await db.execute(select(model_cls).where(model_cls.session_id == session_id))
        ).scalars()
        for row in rows:
            await db.delete(row)
    await db.delete(sess)

    db.add(AuditLog(
        actor_id=user.id, action="delete_session",
        target_type="mdt_session", target_id=session_id,
        payload={"minio_files": n},
    ))
    return {"ok": True, "minio_files_removed": n}


# ---------- SSE 进度 ----------

from fastapi.responses import StreamingResponse  # noqa: E402


@router.get("/{session_id}/progress")
async def session_progress(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    async def event_stream():
        async for evt in subscribe(session_id):
            yield evt

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
