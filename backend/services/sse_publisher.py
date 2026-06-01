"""SSE 进度 + 状态推送 - 通过 Redis pub/sub 把后端事件发回所有订阅的前端 tab。

设计:
- 频道命名:
    progress:{session_id}       会话/会议级 — 进度事件 + 状态变更事件,UUID 不冲突
    progress:user:{user_id}     用户级 — 列表页订阅,收 session_created/deleted/status_changed
- 消息有两种 type:
    progress 事件: 兼容老格式 {stage, percent, message, ts, ...}     Celery 任务推
    state 事件:    {type:"state", kind, session_id?, ts, ...payload}  写操作端点推
- 前端 EventSource 订阅 /api/v1/sessions/{id}/progress 或 /me/stream;
  onmessage 通过 payload.type==='state' 区分。
- subscribe() 既接受 session_id(老入口),也接受完整 channel 字符串(新入口,me/stream 用)。
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Dict

import redis
import redis.asyncio as aioredis

from config import settings
from utils.logger import get_logger

logger = get_logger("sse_publisher")


_sync_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_client


def channel(session_id: str) -> str:
    return f"progress:{session_id}"


def user_channel(user_id: str) -> str:
    return f"progress:user:{user_id}"


def _publish_raw(channel_name: str, payload: Dict[str, Any], log_history: bool = True) -> None:
    try:
        raw = json.dumps(payload, ensure_ascii=False)
        r = _redis()
        r.publish(channel_name, raw)
        if log_history:
            # 写一份到 list,断线后端能恢复最近 50 条
            key = f"progress_log:{channel_name.split(':', 1)[1]}"
            r.lpush(key, raw)
            r.ltrim(key, 0, 49)
            r.expire(key, 3600)
    except redis.RedisError as e:
        logger.warning("sse_publish_failed", channel=channel_name, error=str(e))


def publish(
    session_id: str,
    stage: str,
    percent: int,
    message: str,
    extra: Dict | None = None,
) -> None:
    """Celery worker / agent 调,推一条进度事件(老格式,前端兼容)。"""
    payload = {
        "stage": stage,
        "percent": percent,
        "message": message,
        "ts": time.time(),
    }
    if extra:
        payload.update(extra)
    _publish_raw(channel(session_id), payload, log_history=True)


def publish_state(session_id: str, kind: str, **extra: Any) -> None:
    """写操作端点 / 任务完成时推一条状态变更事件,告诉其他端"该 refetch 了"。

    kind 约定值:
      record_added / record_updated / record_deleted
      voice_updated
      summary_updated / summary_confirmed
      tnm_updated
      opinion_updated
      final_updated
      analysis_done
      session_deleted
    extra 仅放 ID/小字段,绝不放 PII;前端拿到事件后调 GET 拉最新。
    """
    payload: Dict[str, Any] = {
        "type": "state",
        "kind": kind,
        "session_id": session_id,
        "ts": time.time(),
    }
    if extra:
        payload.update(extra)
    # 不写 history — 状态事件靠端到端 refetch 兜底,历史回放反而可能导致重复刷新
    _publish_raw(channel(session_id), payload, log_history=False)


def publish_user_state(user_id: str, kind: str, **extra: Any) -> None:
    """用户级状态事件 — 给 cases 列表页用。

    kind 约定值:
      session_created / session_deleted / session_status_changed
      meeting_created / meeting_deleted / meeting_status_changed
    """
    payload: Dict[str, Any] = {
        "type": "state",
        "kind": kind,
        "user_id": user_id,
        "ts": time.time(),
    }
    if extra:
        payload.update(extra)
    _publish_raw(user_channel(user_id), payload, log_history=False)


async def subscribe(session_id_or_channel: str, *, is_channel: bool = False) -> AsyncIterator[str]:
    """FastAPI route 用 - 异步订阅 + yield SSE 事件字符串。

    用 get_message(timeout=15) 替代 listen(),空闲 15s 发一个 SSE 注释行 (`:keepalive`)
    防中间 NAT/4G/企业代理把空闲 HTTP 连接切断 — 整段 ASR 会有 1-3 分钟无业务事件,
    没有 keepalive 时客户端会看到"假死"。

    Args:
        session_id_or_channel: 默认是 session_id,会被包装成 channel(sid);
            若 is_channel=True 则直接当 channel 名用(给 user_channel 入口)。
    """
    ch = session_id_or_channel if is_channel else channel(session_id_or_channel)
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(ch)

    # 先 flush 最近的进度历史(state 事件不入 history,刷出来也只是 progress)
    log_key = f"progress_log:{ch.split(':', 1)[1]}"
    history = await client.lrange(log_key, 0, 49)
    for raw in reversed(history):
        yield f"data: {raw}\n\n"

    try:
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=15.0
            )
            if msg is None:
                # 15s 空闲 → 发 SSE 注释行;EventSource 规范要求以 ':' 开头的行被忽略
                yield ": keepalive\n\n"
                continue
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if not data:
                continue
            yield f"data: {data}\n\n"
    finally:
        await pubsub.unsubscribe(ch)
        await pubsub.close()
        await client.close()
