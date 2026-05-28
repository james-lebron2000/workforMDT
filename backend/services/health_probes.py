"""依赖健康探针 - 给 /health/deep 用。

每个 probe 函数:
- 返回 `{"name", "ok", "detail", "latency_ms"}` dict
- 自带 2 秒超时 + 异常吞没(返回 ok=False 而非抛错,确保 /health/deep 不会因单点崩溃)
- 所有 probe 必须能并发跑,无共享状态

设计原则:
- "critical" 三件套(postgres/redis/minio)失败 → /health/deep 返 503
- LLM / Volcengine / ASR provider 失败仅标 degraded(不阻塞部署 — 临时离线时医生上传/录音
  仍可入库,只是 AI 分析会失败)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict

from config import settings
from utils.logger import get_logger

logger = get_logger("health_probes")

# 单 probe 超时 — 2s 足够本地依赖,慢的远程 API 让它失败比拖死整个 readyz 好
PROBE_TIMEOUT_S = 2.0


async def _run_with_timeout(
    name: str, fn: Callable[[], Awaitable[Dict[str, Any]]]
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(fn(), timeout=PROBE_TIMEOUT_S)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        result.setdefault("name", name)
        result.setdefault("ok", True)
        result["latency_ms"] = latency_ms
        return result
    except asyncio.TimeoutError:
        return {
            "name": name,
            "ok": False,
            "detail": f"timeout > {PROBE_TIMEOUT_S}s",
            "latency_ms": int(PROBE_TIMEOUT_S * 1000),
        }
    except Exception as e:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.warning("health_probe_error", probe=name, error=type(e).__name__, detail=str(e)[:200])
        return {
            "name": name,
            "ok": False,
            "detail": f"{type(e).__name__}: {str(e)[:120]}",
            "latency_ms": latency_ms,
        }


# ----- Probes -----


async def probe_postgres() -> Dict[str, Any]:
    async def _probe() -> Dict[str, Any]:
        from sqlalchemy import text

        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"detail": "ok"}

    return await _run_with_timeout("postgres", _probe)


async def probe_redis() -> Dict[str, Any]:
    async def _probe() -> Dict[str, Any]:
        # 用同步 redis 客户端在线程池里 PING(redis-py async 也行,但同步更轻量)
        import redis as redis_pkg

        def _ping() -> str:
            client = redis_pkg.Redis.from_url(settings.redis_url, socket_timeout=1.5)
            try:
                return "ok" if client.ping() else "no_pong"
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

        loop = asyncio.get_event_loop()
        detail = await loop.run_in_executor(None, _ping)
        return {"detail": detail, "ok": detail == "ok"}

    return await _run_with_timeout("redis", _probe)


async def probe_minio() -> Dict[str, Any]:
    async def _probe() -> Dict[str, Any]:
        from services.minio_client import get_minio

        def _check() -> bool:
            return get_minio().bucket_exists(settings.minio_bucket)

        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, _check)
        return {
            "detail": f"bucket={settings.minio_bucket} exists" if ok else "bucket missing",
            "ok": ok,
        }

    return await _run_with_timeout("minio", _probe)


async def probe_llm() -> Dict[str, Any]:
    """LLM provider 配置检查 — 仅看 api_key 是否存在,不真调避免计费 + 延迟。"""

    async def _probe() -> Dict[str, Any]:
        from services.llm_client import healthcheck

        status = healthcheck()  # {provider: bool}
        active = settings.llm_provider
        primary_ok = status.get(active, False)
        # fallback 链至少 1 个可用也算 degraded-but-functional
        any_ok = any(status.values())
        return {
            "detail": {
                "active_provider": active,
                "primary_configured": primary_ok,
                "providers": status,
            },
            "ok": any_ok,
        }

    return await _run_with_timeout("llm", _probe)


async def probe_volcengine() -> Dict[str, Any]:
    """火山 OCR(general_basic)凭证可用性 — 仅看 AK/SK 是否填好。"""

    async def _probe() -> Dict[str, Any]:
        ak_ok = bool(settings.volcengine_ak)
        sk_ok = bool(settings.volcengine_sk)
        return {
            "detail": {
                "ak_present": ak_ok,
                "sk_present": sk_ok,
                "purpose": "general_basic OCR",
            },
            "ok": ak_ok and sk_ok,
        }

    return await _run_with_timeout("volcengine_ocr", _probe)


async def probe_ffmpeg() -> Dict[str, Any]:
    """ffmpeg 探活 — 录音 finalize 阶段必须用它把 webm/m4a 转 mp3。
    缺失 → /health/deep 直接 critical,因为 MDT 录音流程会全军覆没。
    """

    async def _probe() -> Dict[str, Any]:
        from services.audio_transcode import ffmpeg_available

        def _check():
            return ffmpeg_available()

        loop = asyncio.get_event_loop()
        ok, detail = await loop.run_in_executor(None, _check)
        return {"detail": detail, "ok": ok}

    return await _run_with_timeout("ffmpeg", _probe)


async def probe_asr_provider() -> Dict[str, Any]:
    """ASR provider 自检 — volcengine 看 doubao_api_key,funasr 看 asr_service_url 可达。"""

    async def _probe() -> Dict[str, Any]:
        provider = (settings.asr_provider or "volcengine").lower()
        if provider == "funasr":
            # 简单 socket 探测,不真调 transcribe
            import socket
            from urllib.parse import urlparse

            parsed = urlparse(settings.asr_service_url)
            host = parsed.hostname or "ai-gpu"
            port = parsed.port or 8002

            def _check() -> bool:
                try:
                    with socket.create_connection((host, port), timeout=1.5):
                        return True
                except OSError:
                    return False

            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, _check)
            return {
                "detail": {
                    "provider": "funasr",
                    "url": settings.asr_service_url,
                    "reachable": ok,
                },
                "ok": ok,
            }
        else:
            from services.volcengine_audio import is_available

            ok, msg = is_available()
            return {
                "detail": {
                    "provider": "volcengine",
                    "model": settings.doubao_audio_model,
                    "status": msg,
                },
                "ok": ok,
            }

    return await _run_with_timeout("asr_provider", _probe)
