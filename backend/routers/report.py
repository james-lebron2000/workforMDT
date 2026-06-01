"""报告 - 字段编辑 + 重新生成单字段 + 导出 Word/PDF/PPT"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.audit import AuditLog
from models.opinion import DepartmentOpinion
from models.recommendation import FinalRecommendation
from models.revision import FieldRevision
from models.session import MdtSession
from models.summary import CaseSummary
from models.tnm import TnmStaging
from models.user import User
from routers._deps import current_user
from routers.consent import require_consent
from services.sse_publisher import publish_state

router = APIRouter()


class FieldEdit(BaseModel):
    field_path: str  # "tnm.basis" / "final.patient_script" / "opinions[0].opinion"
    new_value: Any


# ---------- 编辑 ----------

_TARGET_MAP: Dict[str, Any] = {
    "summary": CaseSummary,
    "tnm": TnmStaging,
    "final": FinalRecommendation,
    "opinions": DepartmentOpinion,
}


@router.patch("/{session_id}/field")
async def edit_field(
    session_id: str,
    edit: FieldEdit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    head, _, rest = edit.field_path.partition(".")
    if head not in _TARGET_MAP and not head.startswith("opinions"):
        raise HTTPException(status_code=400, detail="unknown field root")

    if head in ("summary", "tnm", "final"):
        model = _TARGET_MAP[head]
        row = (
            await db.execute(
                select(model)
                .where(model.session_id == session_id)
                .order_by(model.version.desc())
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{head} not found")
        if not hasattr(row, rest):
            raise HTTPException(status_code=400, detail=f"unknown field: {rest}")
        before = getattr(row, rest)
        setattr(row, rest, edit.new_value)

    elif head.startswith("opinions["):
        # opinions[<id>].field
        try:
            idx = head[len("opinions["):-1]
            opinion = await db.get(DepartmentOpinion, idx)
            if opinion is None or opinion.session_id != session_id:
                raise HTTPException(status_code=404, detail="opinion not found")
            if not hasattr(opinion, rest):
                raise HTTPException(status_code=400, detail=f"unknown field: {rest}")
            before = getattr(opinion, rest)
            setattr(opinion, rest, edit.new_value)
        except IndexError as e:
            raise HTTPException(status_code=400, detail="bad opinion id") from e
    else:
        raise HTTPException(status_code=400, detail="bad field_path")

    db.add(
        FieldRevision(
            session_id=session_id,
            field_path=edit.field_path,
            before=json.dumps(before, ensure_ascii=False, default=str),
            after=json.dumps(edit.new_value, ensure_ascii=False, default=str),
            doctor_id=user.id,
        )
    )
    db.add(AuditLog(
        actor_id=user.id, action="edit_field",
        target_type="mdt_session", target_id=session_id,
        payload={"field": edit.field_path},
    ))
    # 广播 — 其他端 review 页拿到事件后 refetch,即刻看到新值
    publish_state(
        session_id,
        "field_updated",
        field_path=edit.field_path,
        editor_id=user.id,
    )
    return {"ok": True}


# ---------- 重新生成 ----------


class RegenerateRequest(BaseModel):
    section: Literal["summary", "tnm", "opinions", "final", "patient_script"]


@router.post("/{session_id}/regen")
async def regen_section(
    session_id: str,
    payload: RegenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_consent),  # 红线:未签同意书禁止 LLM 重生成
):
    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    from services.celery_app import celery_app

    # MVP 简化:整段重跑分析。生产可分 section 重跑。
    task = celery_app.send_task(
        "tasks.mdt_analysis_task", args=[session_id], queue="mdt"
    )
    return {"ok": True, "task_id": task.id}


# ---------- 导出 ----------


class ExportRequest(BaseModel):
    format: Literal["docx", "pdf", "pptx", "wechat_card"]


# Pre-MDT 会前摘要导出:仅需病史已确认即可,不依赖 TNM/科室意见/治疗建议
_BRIEF_ALLOWED_STATUS = {"summary_confirmed", "recording", "reviewing", "completed"}


@router.post("/{session_id}/export-brief")
async def export_brief(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """导出 MDT 会前病例摘要 PDF — 给与会医生作会前阅读用。

    红线:必须在 status >= summary_confirmed 时才允许导出,
    防止未与患者核对就把可能错的病史分享出去。
    """
    from services.export_service import build_report

    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    if sess.status not in _BRIEF_ALLOWED_STATUS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"必须先完成『病史已与患者核对确认』(当前状态:{sess.status})"
                "才能导出会前摘要"
            ),
        )

    blob, mime, filename = await build_report(db, session_id, "brief_pdf")

    db.add(AuditLog(
        actor_id=user.id, action="export_brief",
        target_type="mdt_session", target_id=session_id,
        payload={"format": "brief_pdf"},
    ))

    import io
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{session_id}/export")
async def export_report(
    session_id: str,
    payload: ExportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """导出 MDT 完整报告(docx/pdf/pptx/wechat_card)。

    红线门禁:
    - QC failed(发现幻觉/承诺词/字段缺失/伪造科室意见)→ 拒绝导出,要求医生先修复。
      防止 AI 出错的报告被直接转发到家属群/外院。
    """
    from services.export_service import build_report

    sess = await db.get(MdtSession, session_id)
    if sess is None or sess.created_by != user.id:
        raise HTTPException(status_code=404, detail="session not found")

    # QC 门禁:查最新版的 FinalRecommendation,qc_status=failed 阻塞导出
    final = (
        await db.execute(
            select(FinalRecommendation)
            .where(FinalRecommendation.session_id == session_id)
            .order_by(FinalRecommendation.version.desc())
        )
    ).scalar_one_or_none()
    if final and final.qc_status == "failed":
        critical = []
        for issue in (final.qc_issues or []):
            if issue.get("severity") in ("critical", "must_fix"):
                msg = issue.get("message") or issue.get("issue") or "未指明"
                critical.append(msg)
        detail_msg = (
            "QC 未通过 — 报告存在严重问题,请在确认页处理后再导出:\n"
            + "\n".join(f"  • {m}" for m in (critical[:5] or ["未明示具体 issue,请联系开发"]))
        )
        raise HTTPException(status_code=400, detail=detail_msg)

    blob, mime, filename = await build_report(db, session_id, payload.format)

    db.add(AuditLog(
        actor_id=user.id, action="export_report",
        target_type="mdt_session", target_id=session_id,
        payload={"format": payload.format, "qc_status": getattr(final, "qc_status", None)},
    ))

    if payload.format == "wechat_card":
        # wechat_card 返 JSON 而非文件
        return {"ok": True, "card": blob.decode("utf-8")}

    import io
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
