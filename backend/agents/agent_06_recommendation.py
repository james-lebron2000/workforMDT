"""Agent 06: 最终建议综合
- 输入: 病例摘要 + TNM + 各科室意见
- 输出: FinalRecommendationSchema (clinical_judgment / exams / treatments / referral / patient_script)
- referral 用规则库做兜底/补全
"""
from __future__ import annotations

from typing import Any, Dict, List

from agents._prompt_loader import render
from schemas.report import FinalRecommendationSchema, ReferralRecommendation
from schemas.tnm import TnmStagingSchema
from services.llm_client import LLMError, chat_json
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session
from utils.referral_rules import match_referrals

logger = get_logger("agent.recommendation")


def _augment_referrals(
    llm_referrals: List[ReferralRecommendation],
    clinical_judgment: str,
    opinions_text: str,
) -> List[ReferralRecommendation]:
    """规则库兜底:文本中命中转移部位关键词的科室,如未在 LLM referrals 里则补一条。"""
    text = f"{clinical_judgment}\n{opinions_text}"
    rule_hits = match_referrals(text)
    have_depts = {r.dept for r in llm_referrals}
    for rule in rule_hits:
        if rule.dept in have_depts:
            continue
        llm_referrals.append(
            ReferralRecommendation(
                dept=rule.dept,
                doctor_hint=rule.doctor_hint,
                reason=rule.reason,
                priority=rule.priority,  # type: ignore[arg-type]
                bring_with=list(rule.bring_with),
            )
        )
    return llm_referrals


def run_recommendation_agent(
    case_summary: Dict[str, Any],
    tnm: Dict[str, Any],
    opinions: List[Dict[str, Any]],
) -> FinalRecommendationSchema:
    """生成最终综合建议。"""
    opinions_text = "\n".join(
        f"{o.get('department')}: {o.get('opinion') or ''} | {o.get('recommendation') or ''}"
        for o in opinions
        if not o.get("is_missing")
    )

    combined = f"{case_summary.get('history_summary','')}\n{opinions_text}"
    with scrub_session(combined) as sess:
        prompt = render(
            "mdt-final-synthesis",
            case_summary=case_summary,
            tnm=tnm,
            opinions=opinions,
        )
        try:
            final = chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是肿瘤 MDT 总结专家。每条治疗建议自动 needs_doctor_confirm=true。"
                            "患者话术严禁出现承诺词。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                schema=FinalRecommendationSchema,
                temperature=0.2,
                max_tokens=4096,
            )
        except LLMError as e:
            logger.error("recommendation_agent_failed", error=str(e))
            # 最低限度兜底:让医生手工完成
            return FinalRecommendationSchema(
                clinical_judgment=f"自动综合失败,请医生手动整理。错误:{e}",
                tnm=TnmStagingSchema.model_validate(tnm),
                suggested_exams=[],
                treatment_plan=[],
                referral=[],
                patient_script=(
                    "本次会诊正在整理中,医生稍后会与您当面沟通详细方案。"
                    "如有不适请及时联系医生。"
                ),
            )

        # 规则库扩充转诊
        final_referrals = _augment_referrals(
            list(final.referral), final.clinical_judgment, opinions_text
        )
        # TNM 强制使用 TNM agent 的输出,防止 recommendation LLM 改写分期(责任分离 + 红线兜底)
        input_tnm = TnmStagingSchema.model_validate(tnm)
        final = final.model_copy(update={
            "referral": final_referrals,
            "tnm": input_tnm,
        })

        restored = sess.restore(final.model_dump())
        return FinalRecommendationSchema.model_validate(restored)
