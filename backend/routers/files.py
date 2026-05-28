"""资料上传 - 走 MinIO presigned PUT,后端不经手大文件"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.record import MedicalRecord
from models.session import MdtSession
from models.user import User
from routers._deps import current_user
from routers.consent import require_consent
from services import minio_client

router = APIRouter()


class PresignRequest(BaseModel):
    session_id: str
    filename: str
    file_type: str = "other"
    mime_type: Optional[str] = None


class PresignResponse(BaseModel):
    record_id: str
    upload_url: str
    file_key: str


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(
    payload: PresignRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止上传
):
    sess = await db.get(MdtSession, payload.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    record_id = str(uuid.uuid4())
    safe_filename = payload.filename.replace("/", "_")
    key = minio_client.session_key(
        payload.session_id, "records", f"{record_id}-{safe_filename}"
    )
    url = minio_client.presigned_put(key)

    record = MedicalRecord(
        id=record_id,
        session_id=payload.session_id,
        file_key=key,
        file_type=payload.file_type,
        mime_type=payload.mime_type,
        ocr_status="pending",
    )
    db.add(record)
    if sess.status == "draft":
        sess.status = "collecting"
    await db.flush()

    return PresignResponse(record_id=record_id, upload_url=url, file_key=key)


@router.get("/{record_id}/download")
async def download_url(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    record = await db.get(MedicalRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    sess = await db.get(MdtSession, record.session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    return {"url": minio_client.presigned_get(record.file_key)}
