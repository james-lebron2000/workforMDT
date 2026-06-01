"""MDT 群组会议 — 多病人/单录音/AI 切分。

设计:
- POST /api/v1/mdt-meetings           创建(传 session_ids,后端预生成 group voice_id + 上传 URL)
- GET  /api/v1/mdt-meetings           列表(只看本医生创建的)
- GET  /api/v1/mdt-meetings/{id}      详情(含成员 session 状态汇总 + split_summary)
- POST /api/v1/mdt-meetings/{id}/finalize   录音完成后调,触发 ASR+切分+各 session 分析
- POST /api/v1/mdt-meetings/{id}/voice/chunk   上传录音分片(等价 /audio/chunk 但绑 meeting)
- POST /api/v1/mdt-meetings/{id}/voice/upload-finalize  拼接 + 转码 mp3,等价 /audio/finalize
- DELETE /api/v1/mdt-meetings/{id}    解散会议(真删:删 MinIO 录音 + 解关联,但保留各 session)

红线松动(已与用户确认):
- 允许 session.status 处于 draft/collecting(未确认病史)加入会议,称"快路录音"。
- 会后医生应回到各 session 补做病史核对(在/cases/[id]/upload 点"病史已确认")。
- 群组录音 ASR 完成 → splitter → 把切分后的 transcript 作为各 session 的
  新 mdt_discussion voice_note(asr_status=done),后续走现有 mdt_analysis_task。
"""
from __future__ import annotations

import io
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.audit import AuditLog
from models.meeting import MdtMeeting, mdt_meeting_sessions
from models.patient import Patient
from models.session import MdtSession
from models.user import User
from models.voice import VoiceNote
from routers._deps import current_user
from routers.consent import require_consent
from services import minio_client
from services.audio_transcode import TranscodeError, transcode_to_mp3
from services.celery_app import celery_app
from services.sse_publisher import publish, publish_state, publish_user_state, subscribe
from utils.logger import get_logger

logger = get_logger("router.meetings")

router = APIRouter()


# ---------- Schemas ----------


class MeetingCreate(BaseModel):
    session_ids: List[str] = Field(..., min_length=1, max_length=20)
    title: Optional[str] = None
    mdt_date: Optional[date] = None


class MeetingMember(BaseModel):
    session_id: str
    patient_code: str
    primary_diagnosis: Optional[str]
    primary_site: Optional[str]
    session_status: str
    has_summary_confirmed: bool
    split_segment_count: Optional[int] = None
    split_confidence: Optional[float] = None
    split_is_missing: Optional[bool] = None


class MeetingOut(BaseModel):
    id: str
    title: Optional[str]
    mdt_date: Optional[date]
    status: str
    group_voice_id: Optional[str]
    audio_finalized: bool = False  # 录音文件已拼接转码完成(file_key 非 placeholder.bin)
    members: List[MeetingMember]
    error: Optional[str]


class MeetingListOut(BaseModel):
    meetings: List[MeetingOut]


# ---------- Helpers ----------


async def _members_payload(
    db: AsyncSession, meeting: MdtMeeting
) -> List[MeetingMember]:
    """组装 meeting 成员摘要(给前端做状态显示)。"""
    rows = (
        await db.execute(
            select(MdtSession, Patient)
            .join(
                mdt_meeting_sessions,
                mdt_meeting_sessions.c.session_id == MdtSession.id,
            )
            .join(Patient, MdtSession.patient_id == Patient.id)
            .where(mdt_meeting_sessions.c.meeting_id == meeting.id)
            .order_by(MdtSession.created_at)
        )
    ).all()
    split_idx = {
        item["session_id"]: item
        for item in (meeting.split_summary or [])
    }
    members: List[MeetingMember] = []
    for sess, patient in rows:
        sp = split_idx.get(sess.id) or {}
        members.append(
            MeetingMember(
                session_id=sess.id,
                patient_code=patient.code,
                primary_diagnosis=patient.primary_diagnosis,
                primary_site=patient.primary_site,
                session_status=sess.status,
                has_summary_confirmed=sess.status
                in (
                    "summary_confirmed",
                    "recording",
                    "analyzing",
                    "reviewing",
                    "completed",
                ),
                split_segment_count=sp.get("segment_count"),
                split_confidence=sp.get("confidence"),
                split_is_missing=sp.get("is_missing"),
            )
        )
    return members


def _meeting_voice_key(meeting_id: str, voice_id: str, kind: str) -> str:
    """群组录音 MinIO key — 单独前缀,便于真删时按 meeting 清理。"""
    safe_kind = kind.replace("/", "_")
    return f"meetings/{meeting_id}/voice/{voice_id}-{safe_kind}"


async def _audio_finalized(db: AsyncSession, meeting: MdtMeeting) -> bool:
    """是否已 upload-finalize(group voice 的 file_key 不再指向 placeholder)。"""
    if not meeting.group_voice_id:
        return False
    voice = await db.get(VoiceNote, meeting.group_voice_id)
    if voice is None:
        return False
    return not (voice.file_key or "").endswith("placeholder.bin")


# ---------- Routes ----------


@router.post("", response_model=MeetingOut)
async def create_meeting(
    payload: MeetingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:同意书门禁
):
    """新建一次群组 MDT 会议。

    创建时即预生成 group_voice_id 与一个空的 VoiceNote 行(voice_type=mdt_group_discussion,
    session_id 取第一个候选 session,便于复用现有 audio 端点的鉴权;后续切分时各 session
    各自有自己的 mdt_discussion VoiceNote)。
    """
    # 校验所有 session_id 都属于本医生
    sessions = list(
        (
            await db.execute(
                select(MdtSession).where(MdtSession.id.in_(payload.session_ids))
            )
        ).scalars()
    )
    if len(sessions) != len(set(payload.session_ids)):
        raise HTTPException(
            status_code=404,
            detail=f"部分 session 不存在或重复(传入 {len(payload.session_ids)} 个,匹配 {len(sessions)})",
        )
    for s in sessions:
        if s.created_by != user.id:
            raise HTTPException(
                status_code=403,
                detail="只能把属于自己的 MDT 病例加入会议",
            )

    meeting = MdtMeeting(
        title=payload.title or f"MDT-{(payload.mdt_date or date.today()).isoformat()}",
        mdt_date=payload.mdt_date,
        status="draft",
        created_by=user.id,
    )
    db.add(meeting)
    await db.flush()

    # 关联表
    for sid in payload.session_ids:
        await db.execute(
            mdt_meeting_sessions.insert().values(
                meeting_id=meeting.id, session_id=sid
            )
        )

    # 预生成 group voice (录音放第一个 session 的桶下,便于鉴权;同时 meeting_id 回填)
    primary_sid = payload.session_ids[0]
    voice_id = str(uuid.uuid4())
    file_key = _meeting_voice_key(meeting.id, voice_id, "placeholder.bin")
    voice = VoiceNote(
        id=voice_id,
        session_id=primary_sid,
        meeting_id=meeting.id,
        file_key=file_key,
        voice_type="mdt_group_discussion",
        asr_status="pending",
    )
    db.add(voice)
    meeting.group_voice_id = voice_id

    db.add(
        AuditLog(
            actor_id=user.id,
            action="create_meeting",
            target_type="mdt_meeting",
            target_id=meeting.id,
            payload={
                "session_count": len(payload.session_ids),
                "session_ids": payload.session_ids,
            },
        )
    )
    await db.flush()

    publish_user_state(
        user.id,
        "meeting_created",
        meeting_id=meeting.id,
        session_count=len(payload.session_ids),
    )

    members = await _members_payload(db, meeting)
    return MeetingOut(
        id=meeting.id,
        title=meeting.title,
        mdt_date=meeting.mdt_date,
        status=meeting.status,
        group_voice_id=meeting.group_voice_id,
        audio_finalized=await _audio_finalized(db, meeting),
        members=members,
        error=meeting.error,
    )


@router.get("", response_model=MeetingListOut)
async def list_meetings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    limit: int = 50,
):
    rows = list(
        (
            await db.execute(
                select(MdtMeeting)
                .where(MdtMeeting.created_by == user.id)
                .order_by(MdtMeeting.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    out: List[MeetingOut] = []
    for m in rows:
        members = await _members_payload(db, m)
        out.append(
            MeetingOut(
                id=m.id,
                title=m.title,
                mdt_date=m.mdt_date,
                status=m.status,
                group_voice_id=m.group_voice_id,
                audio_finalized=await _audio_finalized(db, m),
                members=members,
                error=m.error,
            )
        )
    return MeetingListOut(meetings=out)


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")
    members = await _members_payload(db, meeting)
    return MeetingOut(
        id=meeting.id,
        title=meeting.title,
        mdt_date=meeting.mdt_date,
        status=meeting.status,
        group_voice_id=meeting.group_voice_id,
        audio_finalized=await _audio_finalized(db, meeting),
        members=members,
        error=meeting.error,
    )


@router.delete("/{meeting_id}")
async def delete_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """解散群组会议 — 真删群组录音文件,但保留各 session 病例本身。

    流程:
    1. 删 MinIO 下 meetings/{meeting_id}/ 前缀的所有对象(录音分片+成片)
    2. 删除 group voice_note(切分出去的各 session mdt_discussion 保留,医生可手动清理)
    3. 删除关联 + 删除 meeting 行
    """
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")

    try:
        n = minio_client.remove_prefix(f"meetings/{meeting_id}/")
    except RuntimeError as e:
        db.add(
            AuditLog(
                actor_id=user.id,
                action="delete_meeting_failed",
                target_type="mdt_meeting",
                target_id=meeting_id,
                payload={"reason": "minio_purge_failed", "detail": str(e)[:500]},
            )
        )
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="对象存储清理失败,会议未删除以防孤儿数据。请稍后重试。",
        ) from e

    if meeting.group_voice_id:
        gv = await db.get(VoiceNote, meeting.group_voice_id)
        if gv is not None:
            await db.delete(gv)

    await db.execute(
        mdt_meeting_sessions.delete().where(
            mdt_meeting_sessions.c.meeting_id == meeting_id
        )
    )
    await db.delete(meeting)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="delete_meeting",
            target_type="mdt_meeting",
            target_id=meeting_id,
            payload={"minio_files": n},
        )
    )
    publish_state(meeting_id, "meeting_deleted")
    publish_user_state(user.id, "meeting_deleted", meeting_id=meeting_id)
    return {"ok": True, "minio_files_removed": n}


# ---------- 录音上传(群组专用,避开 /audio/* 的 session_id 鉴权) ----------


@router.post("/{meeting_id}/voice/chunk")
async def upload_meeting_chunk(
    meeting_id: str,
    voice_id: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    mime: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),
):
    """群组录音分片直传 MinIO(每 90s 一片)。"""
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")
    if meeting.group_voice_id != voice_id:
        raise HTTPException(status_code=400, detail="voice_id 与 meeting 不匹配")

    chunk_bytes = await file.read()
    key = _meeting_voice_key(meeting_id, voice_id, f"chunk-{chunk_index:04d}.bin")
    minio_client.put_object(key, chunk_bytes, content_type="application/octet-stream")
    publish(
        meeting_id,
        "upload",
        10,
        f"群组录音第 {chunk_index + 1} 片已上传 ({len(chunk_bytes) / 1024 / 1024:.1f} MB)",
        {"voice_id": voice_id, "chunk_index": chunk_index, "size": len(chunk_bytes)},
    )

    # chunk0 时落 mime
    if mime and chunk_index == 0:
        voice = await db.get(VoiceNote, voice_id)
        if voice is not None and not voice.source_mime:
            voice.source_mime = mime[:64]
            await db.flush()

    if meeting.status == "draft":
        meeting.status = "recording"
        await db.flush()

    return {"ok": True, "chunk_key": key, "size": len(chunk_bytes)}


class MeetingFinalizeRequest(BaseModel):
    voice_id: str
    chunk_count: int
    source_mime: Optional[str] = None


@router.post("/{meeting_id}/voice/upload-finalize")
async def finalize_meeting_voice(
    meeting_id: str,
    payload: MeetingFinalizeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),
):
    """拼接所有分片 + ffmpeg → mp3 + 落 VoiceNote.file_key。

    这一步只是把录音文件做成可被 ASR 服务消化的格式;真正的 ASR + 切分 + 各 session 分析
    由 POST /{meeting_id}/finalize 触发(Celery 异步)。
    """
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")
    if meeting.group_voice_id != payload.voice_id:
        raise HTTPException(status_code=400, detail="voice_id 与 meeting 不匹配")
    voice = await db.get(VoiceNote, payload.voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    if payload.chunk_count <= 0:
        raise HTTPException(status_code=400, detail="chunk_count 必须 > 0")

    # 拼接
    merged = io.BytesIO()
    for i in range(payload.chunk_count):
        key = _meeting_voice_key(meeting_id, payload.voice_id, f"chunk-{i:04d}.bin")
        try:
            merged.write(minio_client.get_object_bytes(key))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"chunk {i} 缺失: {e}"
            ) from e
    raw_bytes = merged.getvalue()
    src_mime = payload.source_mime or voice.source_mime or ""
    publish(
        meeting_id,
        "upload",
        60,
        f"群组录音已收到 {payload.chunk_count} 片,正在合并转码",
        {"voice_id": payload.voice_id, "chunk_count": payload.chunk_count},
    )

    try:
        mp3_bytes = transcode_to_mp3(raw_bytes, src_mime=src_mime)
    except TranscodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"音频转码失败:{e}。请稍后点'重试转写'。",
        ) from e

    final_key = _meeting_voice_key(
        meeting_id, payload.voice_id, f"{payload.voice_id}.mp3"
    )
    minio_client.put_object(final_key, mp3_bytes, content_type="audio/mpeg")
    publish(
        meeting_id,
        "upload",
        90,
        f"群组录音转码完成 ({len(mp3_bytes) / 1024 / 1024:.1f} MB),可开始 AI 切分",
        {"voice_id": payload.voice_id, "mp3_size": len(mp3_bytes)},
    )
    voice.file_key = final_key
    voice.chunk_count = payload.chunk_count
    if src_mime and not voice.source_mime:
        voice.source_mime = src_mime[:64]
    await db.flush()

    # 清理临时 chunk
    for i in range(payload.chunk_count):
        key = _meeting_voice_key(meeting_id, payload.voice_id, f"chunk-{i:04d}.bin")
        minio_client.remove_object(key)

    return {
        "ok": True,
        "file_key": final_key,
        "mp3_size": len(mp3_bytes),
        "raw_size": len(raw_bytes),
    }


@router.post("/{meeting_id}/finalize")
async def finalize_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),
):
    """触发整段 ASR + 语义切分 + 各 session 7-agent 分析。

    前置:必须已 upload-finalize 完成(voice.file_key 指向最终 mp3)。
    本端点立即返回;Celery 异步处理,前端订阅 SSE /api/v1/mdt-meetings/{id}/progress。
    """
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")
    if not meeting.group_voice_id:
        raise HTTPException(status_code=400, detail="尚未生成群组录音")

    voice = await db.get(VoiceNote, meeting.group_voice_id)
    if voice is None or voice.file_key.endswith("placeholder.bin"):
        raise HTTPException(
            status_code=400, detail="请先点'完成录音'拼接转码后再触发分析"
        )

    meeting.status = "transcribing"
    meeting.error = None
    db.add(
        AuditLog(
            actor_id=user.id,
            action="meeting_finalize",
            target_type="mdt_meeting",
            target_id=meeting_id,
            payload={"voice_id": meeting.group_voice_id},
        )
    )
    await db.flush()

    task = celery_app.send_task(
        "tasks.meeting_analyze_task", args=[meeting_id], queue="mdt"
    )
    publish(
        meeting_id,
        "meeting",
        1,
        "群组录音分析任务已提交,等待后台处理",
        {"task_id": task.id, "voice_id": meeting.group_voice_id},
    )
    return {"ok": True, "task_id": task.id, "meeting_id": meeting_id}


# ---------- SSE 进度 ----------

from fastapi.responses import StreamingResponse  # noqa: E402


@router.get("/{meeting_id}/progress")
async def meeting_progress(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """SSE 流 — 共用 sse_publisher,channel = meeting_id(uuid 不会撞 session_id)。"""
    meeting = await db.get(MdtMeeting, meeting_id)
    if meeting is None or meeting.created_by != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")

    async def event_stream():
        async for evt in subscribe(meeting_id):
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
