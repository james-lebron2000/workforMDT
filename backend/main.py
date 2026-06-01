"""TumorBoard AI - FastAPI 主入口"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routers import audio, auth, consent, files, jobs, me, meetings, report, sessions

logging.basicConfig(level=logging.INFO)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", env=settings.app_env, llm_provider=settings.llm_provider)
    # 确保 MinIO bucket 存在 + 生命周期策略已配置(否则首次上传 NoSuchBucket)
    try:
        from services.minio_client import ensure_bucket
        ensure_bucket()
    except Exception as e:
        # 启动期 MinIO 不可用,降级到运行时再试 — 不阻塞进程启动(否则 readyz 撞死锁)
        logger.warning("minio_bucket_init_skip", error=str(e))
    yield
    logger.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="多学科会诊(MDT)病例整理系统",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — 生产应限定 origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env != "production" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """简单的请求日志(不打印 body 防止 PII 泄漏)"""
    response = await call_next(request)
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_error", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "服务器内部错误"},
    )


@app.get("/health")
async def health():
    """浅层健康检查 — 进程存活即返回 200。给 LB / Docker healthcheck 用。"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
    }


@app.get("/health/deep")
async def health_deep():
    """深度健康检查 — 真探所有外部依赖。任一失败返 503,适合部署门禁 (readyz)。

    探测项:Postgres / Redis / MinIO / LLM provider / 火山引擎 OCR / 火山引擎 ASR。
    每项独立 2s 超时,并发执行,总延迟应 < 3s。
    """
    import asyncio

    from services.health_probes import (
        probe_postgres,
        probe_redis,
        probe_minio,
        probe_llm,
        probe_volcengine,
        probe_asr_provider,
        probe_ffmpeg,
    )

    results = await asyncio.gather(
        probe_postgres(),
        probe_redis(),
        probe_minio(),
        probe_llm(),
        probe_volcengine(),
        probe_asr_provider(),
        probe_ffmpeg(),
        return_exceptions=False,
    )

    components = {r["name"]: r for r in results}
    # 关键依赖 (db/redis/minio/ffmpeg) 任一不健康 → 503
    # ffmpeg 列 critical:无它录音 finalize 全军覆没,临床上场即崩
    critical = [components[k]["ok"] for k in ("postgres", "redis", "minio", "ffmpeg")]
    overall_ok = all(critical)
    payload = {
        "status": "ok" if overall_ok else "degraded",
        "app": settings.app_name,
        "components": components,
    }
    if not overall_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


# Routers
PREFIX = "/api/v1"
app.include_router(auth.router, prefix=f"{PREFIX}/auth", tags=["auth"])
app.include_router(consent.router, prefix=f"{PREFIX}/consent", tags=["consent"])
app.include_router(sessions.router, prefix=f"{PREFIX}/sessions", tags=["sessions"])
app.include_router(files.router, prefix=f"{PREFIX}/files", tags=["files"])
app.include_router(audio.router, prefix=f"{PREFIX}/audio", tags=["audio"])
app.include_router(jobs.router, prefix=f"{PREFIX}/jobs", tags=["jobs"])
app.include_router(meetings.router, prefix=f"{PREFIX}/mdt-meetings", tags=["meetings"])
app.include_router(report.router, prefix=f"{PREFIX}/report", tags=["report"])
app.include_router(me.router, prefix=f"{PREFIX}/me", tags=["me"])
