"""自部署 GPU 节点上的 OCR/ASR 服务调用客户端。

通过 HTTP 调 ai-services/ocr-service 和 ai-services/asr-service。
所有图像/音频字节流仅在内网传递,云端 LLM 永不见原文件。

V2 起 OCR 已改为多模态 LLM 直接识图(见 services/celery_tasks.py:ocr_task),
本模块的 ocr_* 函数仅保留供 V1 兼容路径使用,默认情况下 ocr_service_url 为 None,
调用会显式抛错。ASR 仍走自部署 FunASR。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from config import settings
from utils.logger import get_logger

logger = get_logger("infer_client")


_OCR_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_ASR_TIMEOUT = httpx.Timeout(900.0, connect=10.0)  # 30min 录音可能跑较久


def _require_ocr_url() -> str:
    if not settings.ocr_service_url:
        raise RuntimeError(
            "OCR_SERVICE_URL 未配置 — V2 已默认走多模态 LLM,如需 V1 自部署 PaddleOCR "
            "路径,请在 .env 设置 OCR_SERVICE_URL 并启用 ai-services/ocr-service"
        )
    return settings.ocr_service_url


def ocr_image(image_bytes: bytes, filename: str = "img.jpg") -> Dict[str, Any]:
    """[V1 兼容] 调自部署 OCR 服务,返回 {raw_text, tables, blocks, confidence}。"""
    url = f"{_require_ocr_url()}/ocr/image"
    with httpx.Client(timeout=_OCR_TIMEOUT) as cli:
        resp = cli.post(
            url,
            files={"file": (filename, image_bytes, "application/octet-stream")},
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "ocr_call_ok",
            blocks=len(data.get("blocks", [])),
            tables=len(data.get("tables", [])),
            confidence=data.get("confidence"),
        )
        return data


def ocr_pdf(pdf_bytes: bytes, filename: str = "doc.pdf") -> Dict[str, Any]:
    """[V1 兼容] 调自部署 OCR 服务,PDF 直接解析。"""
    url = f"{_require_ocr_url()}/ocr/pdf"
    with httpx.Client(timeout=_OCR_TIMEOUT) as cli:
        resp = cli.post(
            url, files={"file": (filename, pdf_bytes, "application/pdf")}
        )
        resp.raise_for_status()
        return resp.json()


def asr_transcribe(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    hotwords: Optional[List[str]] = None,
    enable_diarization: bool = True,
) -> Dict[str, Any]:
    """调 ASR,返回 {segments: [{speaker_id, start, end, text}], num_speakers, duration}"""
    url = f"{settings.asr_service_url}/asr/transcribe"
    data: Dict[str, Any] = {
        "enable_diarization": "true" if enable_diarization else "false",
    }
    if hotwords:
        data["hotwords"] = ",".join(hotwords)
    with httpx.Client(timeout=_ASR_TIMEOUT) as cli:
        resp = cli.post(
            url,
            files={"file": (filename, audio_bytes, "application/octet-stream")},
            data=data,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "asr_call_ok",
            segments=len(result.get("segments", [])),
            speakers=result.get("num_speakers"),
            duration=result.get("duration"),
        )
        return result


def ocr_healthcheck() -> bool:
    if not settings.ocr_service_url:
        return False  # V2 默认无自部署 OCR
    try:
        with httpx.Client(timeout=5.0) as cli:
            r = cli.get(f"{settings.ocr_service_url}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


def asr_healthcheck() -> bool:
    try:
        with httpx.Client(timeout=5.0) as cli:
            r = cli.get(f"{settings.asr_service_url}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False
