"""Agent 04: TNM 推断
- 输入: 病例摘要 + 原始 OCR 文本
- 输出: TnmStagingSchema(type/T/N/M/overall/basis/uncertainty/confidence)
- 关键: pydantic schema 强约束;basis 必填且不能是占位符
"""
from __future__ import annotations

from typing import Any, List

from agents._prompt_loader import render
from schemas.tnm import TnmStagingSchema
from services.llm_client import LLMError, chat_json
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session

logger = get_logger("agent.tnm")


def run_tnm_agent(
    ocr_texts: List[str],
    case_summary: dict[str, Any],
) -> TnmStagingSchema:
    """推断 TNM 分期。校验失败时降级到 Tx/Nx/M0 + low confidence。"""
    ocr_combined = "\n\n---\n\n".join([t for t in ocr_texts if t])[:60000]

    with scrub_session(ocr_combined) as sess:
        prompt = render(
            "tnm-inference",
            ocr_combined=sess.scrubbed,
            case_summary=case_summary,
        )
        try:
            tnm = chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是肿瘤分期专家,严格按 AJCC 8th 推断。"
                            "禁止编造,缺资料请用 Tx/Nx 并在 uncertainty 中说明。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                schema=TnmStagingSchema,
                temperature=0.1,
            )
            restored = sess.restore(tnm.model_dump())
            return TnmStagingSchema.model_validate(restored)
        except LLMError as e:
            logger.warning("tnm_agent_fallback", error=str(e))
            return TnmStagingSchema(
                tnm_type="cTNM",
                t_stage="Tx",
                n_stage="Nx",
                m_stage="M0",
                overall_stage="unknown",
                basis="LLM 抽取失败,无法确定 TNM,请医生手动评估",
                uncertainty=f"自动分期降级:{e}",
                confidence=0.0,
            )
