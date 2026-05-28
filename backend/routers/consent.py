"""同意书(隐私政策)签署

红线:未签署当前 `policy_version` 的用户不能上传/录音/触发 AI 生成。

接口:
  GET  /api/v1/consent           # 查询当前用户是否已签当前版本
  POST /api/v1/consent           # 用户点击"同意",落库 user_consents

依赖:
  require_consent — 在受保护的写接口注入这个 dep,未签则 403
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.audit import AuditLog
from models.consent import UserConsent
from models.user import User
from routers._deps import current_user
from utils.logger import get_logger

router = APIRouter()
logger = get_logger("router.consent")


class ConsentStatus(BaseModel):
    policy_version: str
    accepted: bool
    accepted_at: Optional[str] = None


class AcceptConsentRequest(BaseModel):
    """用户点击「我已阅读并同意」时,前端 POST 此 body。

    policy_version 必须等于服务端当前版本(防止用户签的是过期文案)。
    """

    policy_version: str


@router.get("", response_model=ConsentStatus)
async def get_consent_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> ConsentStatus:
    """查当前用户是否已签**当前**版本的同意书。"""
    row = (
        await db.execute(
            select(UserConsent)
            .where(
                UserConsent.user_id == user.id,
                UserConsent.policy_version == settings.policy_version,
            )
            .order_by(desc(UserConsent.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    return ConsentStatus(
        policy_version=settings.policy_version,
        accepted=row is not None,
        accepted_at=row.created_at.isoformat() if row else None,
    )


@router.post("", response_model=ConsentStatus)
async def accept_consent(
    payload: AcceptConsentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> ConsentStatus:
    """用户点击「同意」。

    防呆:
      - payload.policy_version 必须等于服务端当前版本,否则 400
      - 同版本重复签同意 → 幂等(不重复落库,返回 already-accepted)
    """
    if payload.policy_version != settings.policy_version:
        raise HTTPException(
            status_code=400,
            detail=(
                f"policy_version 不匹配:你签的是 {payload.policy_version},"
                f"当前版本是 {settings.policy_version},请刷新后重签"
            ),
        )

    # 幂等检查
    existing = (
        await db.execute(
            select(UserConsent).where(
                UserConsent.user_id == user.id,
                UserConsent.policy_version == settings.policy_version,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return ConsentStatus(
            policy_version=settings.policy_version,
            accepted=True,
            accepted_at=existing.created_at.isoformat(),
        )

    # 抓 IP(尊重代理头但兜底 client.host)
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    ua = request.headers.get("user-agent", "")[:512]

    consent = UserConsent(
        user_id=user.id,
        policy_version=settings.policy_version,
        ip=ip,
        user_agent=ua,
    )
    db.add(consent)

    # 审计
    db.add(
        AuditLog(
            actor_id=user.id,
            action="accept_consent",
            target_type="user_consent",
            target_id=user.id,
            payload={"policy_version": settings.policy_version},
        )
    )

    await db.flush()
    logger.info(
        "consent_accepted",
        user_id=user.id,
        policy_version=settings.policy_version,
    )
    return ConsentStatus(
        policy_version=settings.policy_version,
        accepted=True,
        accepted_at=consent.created_at.isoformat() if consent.created_at else None,
    )


# ---------- 给受保护接口的 dependency ----------


async def require_consent(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> User:
    """门禁 dependency — 注入到上传/录音/jobs/report 写接口。

    未签当前版本 → 403,前端引导跳到 /consent 页面。
    """
    row = (
        await db.execute(
            select(UserConsent.id).where(
                UserConsent.user_id == user.id,
                UserConsent.policy_version == settings.policy_version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "policy_version": settings.policy_version,
                "message": (
                    "请先阅读并签署当前版本的隐私政策与使用同意书"
                    f"({settings.policy_version})"
                ),
            },
        )
    return user
