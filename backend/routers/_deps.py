"""Router 通用依赖 - 当前用户(MVP 用 X-Device-Id 头匿名识别)"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User


async def current_user(
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-Id"),
    device_id: Optional[str] = Query(default=None),  # SSE fallback
    db: AsyncSession = Depends(get_db),
) -> User:
    """MVP 临时方案:用 X-Device-Id 头识别用户,不存在则自动创建一个匿名用户。
    SSE/EventSource 不支持自定义 header,允许 query string `device_id` 兜底。
    生产环境替换为微信 OpenID 鉴权。
    """
    did = x_device_id or device_id
    if not did:
        raise HTTPException(status_code=401, detail="missing X-Device-Id header")
    user = (
        await db.execute(select(User).where(User.device_id == did))
    ).scalar_one_or_none()
    if user is None:
        user = User(device_id=did, role="doctor")
        db.add(user)
        await db.flush()
    return user
