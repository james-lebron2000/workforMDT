"""当前用户级 SSE — cases 列表页订阅,收 session_created/deleted/status_changed 事件。

设计:
- 频道 progress:user:{user_id},由 sse_publisher.publish_user_state 推
- payload: {type:"state", kind, user_id, ts, session_id?/meeting_id?/...}
- 前端 EventSource 收到后 refetch list,无需 polling
- 复用 sse_publisher.subscribe(channel, is_channel=True)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from models.user import User
from routers._deps import current_user
from services.sse_publisher import subscribe, user_channel

router = APIRouter()


@router.get("/stream")
async def me_stream(user: User = Depends(current_user)):
    """SSE 流 — 用户级事件总线。

    断线浏览器自动重连;后端用 timeout=15s 发 :keepalive 防中间网关切连。
    """
    ch = user_channel(user.id)

    async def event_stream():
        async for evt in subscribe(ch, is_channel=True):
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
