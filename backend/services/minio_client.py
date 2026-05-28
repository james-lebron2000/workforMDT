"""MinIO 客户端 - 自部署 S3 兼容存储。

设计要点:
- presigned PUT URL:前端直传,后端不经手大文件
- presigned GET URL:短期下载链接(15 min)
- 生命周期策略:原图 30 天归档,365 天彻底删除(对应 retention_days)
- key 命名规范:sessions/{session_id}/records/{record_id}/{filename}
"""
from __future__ import annotations

import io
from datetime import timedelta
from typing import BinaryIO, Optional

from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule, Transition

from config import settings
from utils.logger import get_logger

logger = get_logger("minio_client")


_client: Optional[Minio] = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
    return _client


def ensure_bucket() -> None:
    """初始化时调用 - 创建 bucket + 生命周期策略。"""
    client = get_minio()
    bucket = settings.minio_bucket
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket, location=settings.minio_region)
            logger.info("minio_bucket_created", bucket=bucket)
    except S3Error as e:
        logger.error("minio_bucket_init_failed", bucket=bucket, error=str(e))
        raise

    # 生命周期:30 天后归档,365 天彻底删除
    try:
        lifecycle = LifecycleConfig(
            [
                Rule(
                    ENABLED,
                    rule_filter=Filter(prefix="sessions/"),
                    rule_id="archive-raw-30d",
                    transition=Transition(
                        days=settings.retention_days_raw,
                        storage_class="GLACIER",
                    ),
                ),
                Rule(
                    ENABLED,
                    rule_filter=Filter(prefix="sessions/"),
                    rule_id="delete-raw-365d",
                    expiration=Expiration(days=settings.retention_days_full),
                ),
            ]
        )
        client.set_bucket_lifecycle(bucket, lifecycle)
        logger.info(
            "minio_lifecycle_set",
            bucket=bucket,
            archive_days=settings.retention_days_raw,
            delete_days=settings.retention_days_full,
        )
    except S3Error as e:
        # 部分 MinIO 版本/部署不支持 lifecycle,降级不致命
        logger.warning("minio_lifecycle_skip", error=str(e))


def session_key(session_id: str, kind: str, filename: str) -> str:
    """构造对象 key。kind: records | voice | exports"""
    safe_filename = filename.replace("/", "_").replace("\\", "_")
    return f"sessions/{session_id}/{kind}/{safe_filename}"


def presigned_put(key: str, expires_minutes: int = 30) -> str:
    """生成上传用 presigned PUT URL。"""
    client = get_minio()
    return client.presigned_put_object(
        settings.minio_bucket, key, expires=timedelta(minutes=expires_minutes)
    )


def presigned_get(key: str, expires_minutes: int = 15) -> str:
    """生成下载用 presigned GET URL(短期)。"""
    client = get_minio()
    return client.presigned_get_object(
        settings.minio_bucket, key, expires=timedelta(minutes=expires_minutes)
    )


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """直接上传(后端生成报告时用)。"""
    client = get_minio()
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def put_stream(key: str, stream: BinaryIO, length: int, content_type: str) -> None:
    client = get_minio()
    client.put_object(
        settings.minio_bucket, key, stream, length=length, content_type=content_type
    )


def get_object_bytes(key: str) -> bytes:
    """下载对象返 bytes(供 Celery 任务读取后送 OCR/ASR)。"""
    client = get_minio()
    resp = client.get_object(settings.minio_bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def remove_object(key: str) -> None:
    """真删 - 用户主动删除会话时调用。"""
    client = get_minio()
    try:
        client.remove_object(settings.minio_bucket, key)
        logger.info("minio_object_removed", key=key)
    except S3Error as e:
        logger.warning("minio_object_remove_failed", key=key, error=str(e))


def remove_prefix(prefix: str) -> int:
    """删除 session 下所有对象 — 真删红线。

    设计:
    - 用 S3 批量删除 (DeleteObjects, 一次 ≤1000) 替代逐对象 remove_object,
      性能 + 错误聚合两不耽误。
    - list_objects 内部已分页(SDK 处理 ContinuationToken),所以即使 >1000 对象
      也能完整遍历。
    - 任一对象删除失败 → 抛 RuntimeError,调用方(DELETE 路由)必须捕获并阻止 DB 删除,
      防止"DB 删了但 MinIO 残留"导致原文件孤儿。

    Returns:
        成功删除的对象数。
    """
    from minio.deleteobjects import DeleteObject

    client = get_minio()
    bucket = settings.minio_bucket
    total_removed = 0
    failures: list[dict] = []

    # SDK list_objects 内部分页,这里手动按 1000 个一批攒成 DeleteObjects 请求
    batch: list[DeleteObject] = []
    BATCH_SIZE = 1000

    def _flush(batch_inner: list) -> int:
        if not batch_inner:
            return 0
        errors = list(client.remove_objects(bucket, batch_inner))
        # errors 是迭代器,只有失败的对象才会出现在里面
        ok_count = len(batch_inner) - len(errors)
        for err in errors:
            failures.append(
                {
                    "key": getattr(err, "object_name", "?"),
                    "code": getattr(err, "code", "?"),
                    "message": getattr(err, "message", "?"),
                }
            )
        return ok_count

    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        batch.append(DeleteObject(obj.object_name))
        if len(batch) >= BATCH_SIZE:
            total_removed += _flush(batch)
            batch = []
    total_removed += _flush(batch)

    if failures:
        logger.error(
            "minio_prefix_remove_partial_failure",
            prefix=prefix,
            removed=total_removed,
            failed=len(failures),
            samples=failures[:5],
        )
        raise RuntimeError(
            f"MinIO 真删失败:{len(failures)} 个对象未能删除 "
            f"(prefix={prefix},samples={failures[:3]})"
        )

    logger.info("minio_prefix_removed", prefix=prefix, count=total_removed)
    return total_removed
