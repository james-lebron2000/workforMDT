"""认证 - MVP 用 device_id 临时方案,正式上线接微信 OpenID"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from routers._deps import current_user

router = APIRouter()


class LoginRequest(BaseModel):
    device_id: str
    name: Optional[str] = None
    hospital: Optional[str] = None
    dept: Optional[str] = None


class LoginResponse(BaseModel):
    user_id: str
    device_id: str
    name: Optional[str] = None


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    if not payload.device_id.strip():
        raise HTTPException(status_code=400, detail="device_id required")
    user = (
        await db.execute(select(User).where(User.device_id == payload.device_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            device_id=payload.device_id,
            name=payload.name,
            hospital=payload.hospital,
            dept=payload.dept,
            role="doctor",
        )
        db.add(user)
        await db.flush()
    else:
        if payload.name:
            user.name = payload.name
        if payload.hospital:
            user.hospital = payload.hospital
        if payload.dept:
            user.dept = payload.dept
    return LoginResponse(user_id=user.id, device_id=user.device_id or "", name=user.name)


@router.get("/me", response_model=LoginResponse)
async def me(user: User = Depends(current_user)):
    return LoginResponse(user_id=user.id, device_id=user.device_id or "", name=user.name)
