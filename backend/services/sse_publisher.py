"""SSE 进度推送 - 通过 Redis pub/sub 把 Celery 进度发回前端。

设计:
- 频道命名: progress:{session_id}
- 消息格式: {stage, percent, message, ts}
- 前端 EventSource 订阅 /api/v1/sessions/{id}/progress
- 后端 FastAPI 路由订阅 Redis 并 yield 给客户端
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator, Dict

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


def publish(
    session_id: str,
    stage: str,
    percent: int,
    message: str,
    extra: Dict | None = None,
) -> None:
    """Celery worker / agent 调,推一条进度。"""
    payload = {
        "stage": stage,
        "percent": percent,
        "message": message,
        "ts": time.time(),
    }
    if extra:
        payload.update(extra)
    try:
        _redis().publish(channel(session_id), json.dumps(payload, ensure_ascii=False))
        # 同时写一份到 list,断线后端能恢复最近 50 条
        key = f"progress_log:{session_id}"
        r = _redis()
        r.lpush(key, json.dumps(payload, ensure_ascii=False))
        r.ltrim(key, 0, 49)
        r.expire(key, 3600)
    except redis.RedisError as e:
        logger.warning("sse_publish_failed", session_id=session_id, error=str(e))


async def subscribe(session_id: str) -> AsyncIterator[str]:
    """FastAPI route 用 - 异步订阅 + yield SSE 事件字符串。"""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    ch = channel(session_id)
    await pubsub.subscribe(ch)

    # 先 flush 最近的历史(若有)
    log_key = f"progress_log:{session_id}"
    history = await client.lrange(log_key, 0, 49)
    for raw in reversed(history):
        yield f"data: {raw}\n\n"

    try:
        async for msg in pubsub.listen():
            if msg is None:
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
