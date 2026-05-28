"""Agent 01: 视觉 OCR + 结构化抽取(多模态 LLM 一次性完成)

- 输入: 一份资料的图片列表(单图 / PDF 各页转 PNG 后的 list[(bytes, mime)])
- 输出: {raw_text, structured, confidence}
- 关键约束:
  * 图片像素无法脱敏 — UX 引导医生拍照前遮挡 PII;
  * 返回的 raw_text 在交给下游 case_summary/tnm/qc agent 前必须经 scrub_session 脱敏;
  * structured 是诊疗信息,不应含 PII(prompt 已明确禁止)。

历史:V1 走"PaddleOCR → 文本 LLM"两段式;V2 改为多模态 LLM 单段,
省去一个 GPU 自部署依赖。保留 run_ocr_agent 接口供旧路径降级测试用。
"""
from __future__ import annotations

from typing import Any, List, Tuple

from agents._prompt_loader import render
from schemas.ocr import OcrExtraction, OcrVisionResult
from services.llm_client import (
    LLMError,
    build_image_content,
    chat_json,
    chat_vision_json,
)
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session

logger = get_logger("agent.ocr")


def run_ocr_vision_agent(images: List[Tuple[bytes, str]]) -> dict[str, Any]:
    """多模态版:一次性识别 + 结构化。

    images: [(image_bytes, mime), ...],例如 [(jpg_bytes, "image/jpeg")] 或 PDF 拆页后的多张 PNG
    返回 dict 含 raw_text(供后续 scrub)/structured(可直接落 MedicalRecord.structured)/confidence
    """
    if not images:
        logger.warning("ocr_vision_no_images")
        return {
            "raw_text": "",
            "structured": OcrExtraction(file_type="other").model_dump(),
            "confidence": 0.0,
        }

    text_prompt = render("ocr-vision")
    # 图片在前,文字指令在后 — 大多数 vision 模型对此顺序表现更稳
    content_blocks = build_image_content(images, text_prompt, text_first=False)

    try:
        result = chat_vision_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是医学资料 OCR + 结构化抽取助手,"
                        "严格按 JSON schema 输出,不编造图中不存在的内容。"
                    ),
                },
                {"role": "user", "content": content_blocks},
            ],
            schema=OcrVisionResult,
            temperature=0.1,
            max_tokens=8192,  # 单份资料 raw_text 可能较长(多页拼接)
        )
    except LLMError as e:
        logger.error("ocr_vision_llm_failed", error=str(e), n_images=len(images))
        return {
            "raw_text": "",
            "structured": OcrExtraction(
                file_type="other", notes=f"视觉 LLM 失败:{e}"
            ).model_dump(),
            "confidence": 0.0,
        }

    logger.info(
        "ocr_vision_ok",
        n_images=len(images),
        raw_text_len=len(result.raw_text),
        confidence=result.confidence,
        file_type=result.extraction.file_type,
    )
    return {
        "raw_text": result.raw_text,
        "structured": result.extraction.model_dump(),
        "confidence": result.confidence,
    }


# ---------- 旧路径:文本 LLM 抽取(保留,供 PaddleOCR/外部 OCR 接入或离线测试用) ----------


def run_ocr_agent(raw_text: str, tables: List[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对已有的 OCR 文本做结构化抽取(V1 路径,保留兼容)。"""
    if not raw_text or not raw_text.strip():
        logger.warning("ocr_agent_empty_text")
        return OcrExtraction(file_type="other").model_dump()

    with scrub_session(raw_text) as session:
        prompt = render(
            "ocr-extract",
            raw_text=session.scrubbed,
            tables_json=tables or [],
        )
        try:
            extraction = chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": "你是医学资料结构化抽取助手,严格遵守 schema。",
                    },
                    {"role": "user", "content": prompt},
                ],
                schema=OcrExtraction,
                temperature=0.1,
            )
        except LLMError as e:
            logger.error("ocr_agent_llm_failed", error=str(e))
            return OcrExtraction(
                file_type="other", notes=f"LLM 抽取失败:{e}"
            ).model_dump()

        restored = session.restore(extraction.model_dump())
        return restored
