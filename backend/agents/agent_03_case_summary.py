"""Agent 03: 病例摘要
- 输入: OCR 文本 + 结构化字段 + 患者诉求转写
- 输出: CaseSummarySchema(chief_need / history_summary / timeline / current_problem / mdt_questions)
"""
from __future__ import annotations

from typing import Any, List

from agents._prompt_loader import render
from schemas.summary import CaseSummarySchema
from services.llm_client import chat_json
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session

logger = get_logger("agent.case_summary")


def run_case_summary_agent(
    ocr_texts: List[str],
    patient_request_text: str,
    structured_records: List[dict[str, Any]],
) -> CaseSummarySchema:
    """生成病例摘要。"""
    ocr_combined = "\n\n---\n\n".join([t for t in ocr_texts if t])[:60000]

    combined_for_scrub = f"{ocr_combined}\n\n[患者诉求]\n{patient_request_text or ''}"

    with scrub_session(combined_for_scrub) as sess:
        # 把 scrubbed 内容拆回去
        if "[患者诉求]" in sess.scrubbed:
            ocr_part, _, pat_part = sess.scrubbed.partition("\n\n[患者诉求]\n")
        else:
            ocr_part, pat_part = sess.scrubbed, ""

        prompt = render(
            "case-summary",
            ocr_combined=ocr_part,
            patient_request=pat_part,
            structured_records=structured_records or [],
        )
        result = chat_json(
            messages=[
                {
                    "role": "system",
                    "content": "你是中国肿瘤 MDT 协调员的病例整理助手,严格遵守 schema 与不编造原则。",
                },
                {"role": "user", "content": prompt},
            ],
            schema=CaseSummarySchema,
            temperature=0.2,
        )
        restored = sess.restore(result.model_dump())
        return CaseSummarySchema.model_validate(restored)
