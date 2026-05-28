"""任务触发 - 投递到 Celery 队列(异步)"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.record import MedicalRecord
from models.session import MdtSession
from models.user import User
from models.voice import VoiceNote
from routers._deps import current_user
from routers.consent import require_consent
from services.celery_app import celery_app

router = APIRouter()


class JobAck(BaseModel):
    ok: bool
    task_id: str


@router.post("/ocr/{record_id}", response_model=JobAck)
async def trigger_ocr(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止触发 AI 生成
):
    record = await db.get(MedicalRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    sess = await db.get(MdtSession, record.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    task = celery_app.send_task("tasks.ocr_task", args=[record_id], queue="ocr")
    return JobAck(ok=True, task_id=task.id)


@router.post("/asr/{voice_id}", response_model=JobAck)
async def trigger_asr(
    voice_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止触发 AI 生成
):
    voice = await db.get(VoiceNote, voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    sess = await db.get(MdtSession, voice.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    task = celery_app.send_task("tasks.asr_task", args=[voice_id], queue="asr")
    return JobAck(ok=True, task_id=task.id)


@router.post("/summary/{session_id}", response_model=JobAck)
async def trigger_summary(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止触发 AI 生成
):
    """触发 MDT 前的"病史汇总" - 只跑 case_summary agent。

    前置条件:至少一份 OCR 完成的资料。
    """
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    ready = (
        await db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.session_id == session_id)
            .where(MedicalRecord.ocr_status == "done")
        )
    ).first()
    if ready is None:
        raise HTTPException(
            status_code=400,
            detail="尚无 OCR 完成的资料,请先上传 1 份化验单/病理/影像",
        )

    task = celery_app.send_task(
        "tasks.summary_task", args=[session_id], queue="mdt"
    )
    return JobAck(ok=True, task_id=task.id)


@router.post("/analyze/{session_id}", response_model=JobAck)
async def trigger_analyze(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止触发 AI 生成
):
    """触发 MDT 综合分析 - 04/05/06/07 Agent 串联。

    前置条件:
    - session 状态为 summary_confirmed(医生与患者核对过病史)
    - 至少一条 mdt_discussion 录音 ASR 完成
    """
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    if sess.status not in ("summary_confirmed", "recording", "reviewing"):
        raise HTTPException(
            status_code=400,
            detail="请先完成病史摘要的核对(点'病史已与患者确认')再进入 MDT 分析",
        )

    # 校验前置:至少一条 mdt_discussion done
    ready = (
        await db.execute(
            select(VoiceNote)
            .where(VoiceNote.session_id == session_id)
            .where(VoiceNote.voice_type == "mdt_discussion")
            .where(VoiceNote.asr_status == "done")
        )
    ).first()
    if ready is None:
        raise HTTPException(
            status_code=400,
            detail="尚未有完成的 MDT 录音转写,请先完成录音 + ASR",
        )

    task = celery_app.send_task(
        "tasks.mdt_analysis_task", args=[session_id], queue="mdt"
    )
    return JobAck(ok=True, task_id=task.id)
