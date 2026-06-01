"""录音上传 - 分片直传 MinIO,后端只做记录"""
from __future__ import annotations

import io
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.session import MdtSession
from models.user import User
from models.voice import VoiceNote
from routers._deps import current_user
from routers.consent import require_consent
from services import minio_client
from services.audio_transcode import TranscodeError, transcode_to_mp3
from services.sse_publisher import publish
from utils.logger import get_logger

logger = get_logger("router.audio")

router = APIRouter()


class VoicePresignRequest(BaseModel):
    session_id: str
    filename: str
    voice_type: Literal["patient_request", "mdt_discussion"]


class VoicePresignResponse(BaseModel):
    voice_id: str
    upload_url: str
    file_key: str


@router.post("/presign", response_model=VoicePresignResponse)
async def presign_voice(
    payload: VoicePresignRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止录音
):
    sess = await db.get(MdtSession, payload.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    voice_id = str(uuid.uuid4())
    safe_filename = payload.filename.replace("/", "_")
    key = minio_client.session_key(
        payload.session_id, "voice", f"{voice_id}-{safe_filename}"
    )
    url = minio_client.presigned_put(key)

    voice = VoiceNote(
        id=voice_id,
        session_id=payload.session_id,
        file_key=key,
        voice_type=payload.voice_type,
        asr_status="pending",
    )
    db.add(voice)
    if payload.voice_type == "mdt_discussion" and sess.status in ("draft", "collecting"):
        sess.status = "recording"
    await db.flush()

    return VoicePresignResponse(voice_id=voice_id, upload_url=url, file_key=key)


@router.post("/chunk")
async def upload_voice_chunk(
    session_id: str = Form(...),
    voice_id: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    mime: Optional[str] = Form(None),  # 前端 MediaRecorder.mimeType,用于 finalize 转码
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止录音
):
    """录音分片上传(每 90s 一片防丢失)。

    红线:即录即存,任一片落 MinIO 即意味"会议已保存,不会丢"。
    前端应在 form 里附带 mime(MediaRecorder.mimeType),finalize 时按此转码成 mp3。
    """
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    chunk_bytes = await file.read()
    key = minio_client.session_key(
        session_id, "voice", f"{voice_id}-chunk-{chunk_index:04d}.bin"
    )
    minio_client.put_object(key, chunk_bytes, content_type="application/octet-stream")
    publish(
        session_id,
        "upload",
        10,
        f"录音第 {chunk_index + 1} 片已上传 ({len(chunk_bytes) / 1024 / 1024:.1f} MB)",
        {"voice_id": voice_id, "chunk_index": chunk_index, "size": len(chunk_bytes)},
    )

    # 第一片到来时记录 mime,finalize 据此选 ffmpeg 容器类型
    if mime and chunk_index == 0:
        voice = await db.get(VoiceNote, voice_id)
        if voice is not None and not voice.source_mime:
            voice.source_mime = mime[:64]  # 截断防超长
            await db.flush()

    return {"ok": True, "chunk_key": key, "size": len(chunk_bytes)}


class VoiceFinalizeRequest(BaseModel):
    session_id: str
    voice_id: str
    chunk_count: int
    final_filename: Optional[str] = None
    source_mime: Optional[str] = None  # 前端 MediaRecorder.mimeType,优先于 chunk0 上报值


@router.post("/finalize")
async def finalize_voice(
    payload: VoiceFinalizeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止录音
):
    """把所有分片按顺序拼接 → ffmpeg 转码成 16kHz mono mp3 → 上传 MinIO。

    红线:
    - 必须经过 ffmpeg 转码;直接拼字节会让火山豆包音频理解 API 拒收(只接 mp3/wav)。
    - 转码失败立即抛 500,VoiceNote.asr_status 留 pending,医生可点"重试转写"
      触发 finalize 再来一次,不会出现"录了但永远转不了"的孤儿。
    """
    voice = await db.get(VoiceNote, payload.voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    sess = await db.get(MdtSession, voice.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=403, detail="forbidden")

    if payload.chunk_count <= 0:
        raise HTTPException(status_code=400, detail="chunk_count 必须 > 0")

    # 1. 拼接所有 chunk
    merged = io.BytesIO()
    for i in range(payload.chunk_count):
        chunk_key = minio_client.session_key(
            payload.session_id, "voice", f"{payload.voice_id}-chunk-{i:04d}.bin"
        )
        try:
            data = minio_client.get_object_bytes(chunk_key)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"chunk {i} 缺失: {e}"
            ) from e
        merged.write(data)
    raw_bytes = merged.getvalue()
    raw_size = len(raw_bytes)

    # 2. 选定源 mime:优先 finalize payload,其次 chunk0 记录的 voice.source_mime
    src_mime = payload.source_mime or voice.source_mime or ""
    logger.info(
        "voice_finalize_start",
        voice_id=payload.voice_id,
        chunks=payload.chunk_count,
        raw_bytes=raw_size,
        src_mime=src_mime,
    )
    publish(
        str(voice.session_id),
        "upload",
        60,
        f"录音已收到 {payload.chunk_count} 片,正在合并转码",
        {"voice_id": payload.voice_id, "chunk_count": payload.chunk_count},
    )

    # 3. ffmpeg 转码为标准 mp3
    try:
        mp3_bytes = transcode_to_mp3(raw_bytes, src_mime=src_mime)
    except TranscodeError as e:
        logger.error(
            "voice_finalize_transcode_failed",
            voice_id=payload.voice_id,
            err=str(e)[:200],
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"音频转码失败:{e}。请稍后在录音卡片点'重试转写';"
                "若持续失败,请检查录音是否为空或联系管理员。"
            ),
        ) from e

    # 4. 上传转码后的 mp3
    final_name = payload.final_filename or f"{payload.voice_id}.mp3"
    if not final_name.lower().endswith(".mp3"):
        final_name = f"{final_name}.mp3"
    final_key = minio_client.session_key(
        payload.session_id, "voice", f"{payload.voice_id}-{final_name}"
    )
    minio_client.put_object(final_key, mp3_bytes, content_type="audio/mpeg")
    publish(
        str(voice.session_id),
        "upload",
        90,
        f"录音转码完成 ({len(mp3_bytes) / 1024 / 1024:.1f} MB),准备提交 ASR",
        {"voice_id": payload.voice_id, "mp3_size": len(mp3_bytes)},
    )

    voice.file_key = final_key
    voice.chunk_count = payload.chunk_count
    if src_mime and not voice.source_mime:
        voice.source_mime = src_mime[:64]
    await db.flush()

    # 5. 清理临时 chunk(转码已成功,原始分片可释放)
    for i in range(payload.chunk_count):
        chunk_key = minio_client.session_key(
            payload.session_id, "voice", f"{payload.voice_id}-chunk-{i:04d}.bin"
        )
        minio_client.remove_object(chunk_key)

    logger.info(
        "voice_finalize_ok",
        voice_id=payload.voice_id,
        raw_bytes=raw_size,
        mp3_bytes=len(mp3_bytes),
    )
    return {
        "ok": True,
        "file_key": final_key,
        "raw_size": raw_size,
        "mp3_size": len(mp3_bytes),
    }
