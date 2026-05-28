"""Agent 05: MDT 各科室意见提炼(MVP 最核心新逻辑)

两阶段:
1. speaker → 科室归类: LLM 基于发言语义判断每个匿名 speaker 属于哪个科室
2. 按科室合并发言 → 提炼意见(opinion/rationale/recommendation/evidence)

关键安全:
- 任一 6 核心科未发言 → DepartmentOpinion(is_missing=true)
- 评估失败兜底:返回所有 6 科室的 is_missing=true 记录
"""
from __future__ import annotations

from typing import Any, Dict, List

from agents._prompt_loader import render
from agents.agent_02_asr import aggregate_by_speaker
from schemas.asr import SpeakerClassificationResult
from schemas.opinion import (
    CORE_DEPARTMENTS,
    DepartmentOpinionSchema,
    OpinionsExtractionResult,
)
from services.llm_client import LLMError, chat_json
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session

logger = get_logger("agent.mdt_opinion")


def _classify_speakers(
    speaker_chunks: Dict[str, str],
) -> Dict[str, str]:
    """LLM 把 speaker_id 归到科室。返回 {SP01: '外科'}。"""
    if not speaker_chunks:
        return {}
    prompt = render(
        "mdt-speaker-classify",
        speaker_chunks=speaker_chunks,
    )
    try:
        result = chat_json(
            messages=[
                {"role": "system", "content": "你是 MDT 录音整理助手,严格遵守 schema。"},
                {"role": "user", "content": prompt},
            ],
            schema=SpeakerClassificationResult,
            temperature=0.1,
        )
        return {a.speaker: a.department for a in result.assignments}
    except LLMError as e:
        logger.warning("speaker_classify_failed", error=str(e))
        return {sp: "未知" for sp in speaker_chunks}


def _extract_opinions(
    dept_chunks: Dict[str, str],
    case_summary: Dict[str, Any],
) -> OpinionsExtractionResult:
    """各科室意见提炼。"""
    prompt = render(
        "mdt-dept-opinion",
        dept_chunks=dept_chunks,
        case_summary=case_summary,
    )
    return chat_json(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 MDT 会议纪要专家。"
                    "未发言的科室必须 is_missing=true,不允许伪造意见。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        schema=OpinionsExtractionResult,
        temperature=0.2,
    )


def _ensure_core_departments(
    opinions: List[DepartmentOpinionSchema],
) -> List[DepartmentOpinionSchema]:
    """对未覆盖的核心 6 科室填充 is_missing=true 记录。"""
    have = {op.department for op in opinions}
    for dept in CORE_DEPARTMENTS:
        if dept not in have:
            opinions.append(
                DepartmentOpinionSchema(
                    department=dept,  # type: ignore[arg-type]
                    is_missing=True,
                    confidence="low",
                    evidence_source=None,
                )
            )
    return opinions


def run_mdt_opinion_agent(
    segments: List[Dict[str, Any]],
    case_summary: Dict[str, Any],
) -> List[DepartmentOpinionSchema]:
    """MVP 核心:把 ASR 分组的多说话人录音 → 各科室结构化意见。"""
    if not segments:
        logger.info("mdt_opinion_no_segments")
        return _ensure_core_departments([])

    speaker_chunks = aggregate_by_speaker(segments)
    # 拼接做 PII 脱敏(LLM 每次都重新打 placeholder,所以分次也安全;此处合并便于一次性脱敏)
    combined = "\n\n".join(f"[{sp}] {txt}" for sp, txt in speaker_chunks.items())

    with scrub_session(combined) as sess:
        # 把脱敏后的文本拆回每个 speaker
        scrubbed_chunks: Dict[str, str] = {}
        for sp in speaker_chunks:
            marker = f"[{sp}]"
            idx = sess.scrubbed.find(marker)
            if idx < 0:
                scrubbed_chunks[sp] = ""
                continue
            # 找下一个 [...] 或结尾
            after = sess.scrubbed[idx + len(marker):]
            next_idx = len(after)
            for other_sp in speaker_chunks:
                if other_sp == sp:
                    continue
                pos = after.find(f"\n\n[{other_sp}]")
                if 0 <= pos < next_idx:
                    next_idx = pos
            scrubbed_chunks[sp] = after[:next_idx].strip()

        # Stage 1: 归类
        sp_to_dept = _classify_speakers(scrubbed_chunks)

        # 按科室聚合
        dept_chunks: Dict[str, str] = {}
        for sp, txt in scrubbed_chunks.items():
            dept = sp_to_dept.get(sp, "未知")
            if dept_chunks.get(dept):
                dept_chunks[dept] += f"\n[{sp}] {txt}"
            else:
                dept_chunks[dept] = f"[{sp}] {txt}"

        # Stage 2: 各科室意见
        try:
            extraction = _extract_opinions(dept_chunks, case_summary)
            opinions = list(extraction.opinions)
        except LLMError as e:
            logger.warning("opinion_extract_failed", error=str(e))
            opinions = []

        opinions = _ensure_core_departments(opinions)

        # 回填 PII placeholder
        restored = [
            DepartmentOpinionSchema.model_validate(sess.restore(op.model_dump()))
            for op in opinions
        ]
        return restored
