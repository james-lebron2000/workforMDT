"""火山引擎通用文字识别(OCRNormal)客户端。

SDK 1.0.184 实际暴露的 action 是 `OCRNormal`(`svc.ocr_normal(...)`),功能等同于
火山文档里的"通用文字识别"。注意:
  * body key 为 `image_base64`,不是 `image`;
  * 失败码 50400 "Access Denied" 通常意味着该 AccessKey 没有 CV/OCR 服务的策略授权
    — 去 https://console.volcengine.com/iam/keymanage 给子用户加 CVFullAccess。

鉴权: AccessKey ID / Secret(火山签名),由官方 volcengine SDK 自动处理。
计费: CV 视觉智能套餐 5000 次/月免费,超出 0.005 元/次(2026-05)。

为什么用专用 OCR 而不是多模态 LLM:
- 纯文字识别准确率更高(化验单/病理报告对数字、单位最敏感);
- 单价 = 多模态 LLM 调用成本的 1-2 个数量级以下;
- 返回结构稳定(words + word_boxes),便于下游表格还原。

下游:OCR 返回的 raw_text 在传给 LLM(agent_03/04/05/06)前,
必须用 utils.pii_scrubber.scrub_session 闭包脱敏患者姓名/身份证/手机号。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from config import settings
from utils.logger import get_logger

logger = get_logger("volcengine_ocr")


class VolcOcrError(RuntimeError):
    """火山 OCR 调用相关异常(配置缺失/SDK 未装/远端 4xx-5xx)。"""


def _make_visual_service():
    """懒加载 SDK 实例 — 避免不用 OCR 路径的部署(纯 vision LLM)也必须安装 SDK。"""
    if not (settings.volcengine_ak and settings.volcengine_sk):
        raise VolcOcrError(
            "VOLCENGINE_AK / VOLCENGINE_SK 未配置 — 请在 .env 填火山引擎 AK/SK"
        )
    try:
        from volcengine.visual.VisualService import VisualService  # type: ignore
    except ImportError as e:
        raise VolcOcrError(
            "volcengine SDK 未安装 — 请运行 `pip install volcengine`"
        ) from e
    svc = VisualService()
    svc.set_ak(settings.volcengine_ak)
    svc.set_sk(settings.volcengine_sk)
    return svc


def ocr_image(image_bytes: bytes, *, need_word_box: bool = False) -> Dict[str, Any]:
    """对单张图片做通用文字识别。

    返回:
        {
            "raw_text": str,            # words 用 \n 拼接的全文
            "words":    list[str],      # 原始行序
            "word_boxes": list[list],   # 若 need_word_box=True 才有
            "confidence": float,        # 火山 general_basic 不返回单独置信度,默认 0.9
        }
    """
    svc = _make_visual_service()

    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    body: Dict[str, Any] = {"image_base64": img_b64}
    if need_word_box:
        body["need_word_box"] = True

    try:
        resp = svc.ocr_normal(body)
    except Exception as e:  # SDK 自己的 HTTP/签名异常
        raise VolcOcrError(f"火山 OCR 调用失败: {type(e).__name__}: {e}") from e

    code = resp.get("code") if isinstance(resp, dict) else None
    if code != 10000:
        msg = (resp or {}).get("message") or ""
        if code == 50400 or "Access Denied" in str(msg):
            raise VolcOcrError(
                "火山 OCR Access Denied (code=50400) — AccessKey 没有 CV/OCR 服务授权。"
                "去 https://console.volcengine.com/iam/keymanage 给子用户加 CVFullAccess。"
            )
        raise VolcOcrError(f"火山 OCR 返回错误: code={code} msg={msg}")

    data = resp.get("data") or {}
    words: List[str] = list(data.get("words") or [])
    word_boxes = list(data.get("word_boxes") or []) if need_word_box else []
    raw_text = "\n".join(words)

    logger.info(
        "volcengine_ocr_ok",
        n_words=len(words),
        text_len=len(raw_text),
    )

    return {
        "raw_text": raw_text,
        "words": words,
        "word_boxes": word_boxes,
        "confidence": 0.9,
    }


def ocr_pages(
    pages: List[tuple[bytes, str]],
    *,
    page_separator: str = "\n\n---page-break---\n\n",
) -> Dict[str, Any]:
    """多张图(同一份资料的连续页)合并 OCR,返回拼接后的 raw_text + 每页明细。

    输入:[(image_bytes, mime), ...]  —— mime 仅用于日志,SDK 内部以 base64 字节判断
    """
    parts: List[str] = []
    details: List[Dict[str, Any]] = []
    for i, (b, mime) in enumerate(pages):
        try:
            r = ocr_image(b)
        except VolcOcrError as e:
            logger.warning(
                "volcengine_ocr_page_failed",
                index=i,
                mime=mime,
                error=str(e)[:200],
            )
            details.append({"index": i, "error": str(e)})
            parts.append(f"[第 {i + 1} 页 OCR 失败:{e}]")
            continue
        parts.append(r["raw_text"])
        details.append(
            {
                "index": i,
                "n_words": len(r["words"]),
                "text_len": len(r["raw_text"]),
            }
        )
    return {
        "raw_text": page_separator.join(parts),
        "pages": details,
        "n_pages": len(pages),
    }


def healthcheck() -> bool:
    """有 AK/SK + SDK 已装 即视为可用(不真调,免计费)。"""
    if not (settings.volcengine_ak and settings.volcengine_sk):
        return False
    try:
        import volcengine  # noqa: F401
    except ImportError:
        return False
    return True
