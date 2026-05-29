"""Agent 08: 多病例语义切分(MVP 新功能核心)

输入:
- 整段 MDT 录音的 ASR transcript(List[TranscriptSegment])
- 本次会议讨论的候选患者列表(List[MeetingCandidate],含 session_id/patient_code/诊断)

输出:
- {session_id: [TranscriptSegment]}  — 每个 session_id 对应一段子转写
- 未归属(开场白/无关闲聊)落到 "__unassigned__" key

红线:
- 不伪造发言:某 candidate 完全未被讨论 → 返回 is_missing=true + segments=[]
- 一条 ASR segment 至多归到一个 candidate(互斥);LLM 重复归类时取第一个出现
- 严禁改写 segment.text / start / end;若 LLM 返回的 text 与原 segment 不一致,以原 segment 为准
- LLM 失败 → 兜底:全部 segments 落到第一个 candidate(防止全军覆没),并打 confidence=0
"""
from __future__ import annotations

from typing import Any, Dict, List

from agents._prompt_loader import render
from schemas.asr import TranscriptSegment
from schemas.meeting import MeetingCandidate, MeetingSplit, MeetingSplitResult
from services.llm_client import LLMError, chat_json
from utils.logger import get_logger
from utils.pii_scrubber import scrub_session

logger = get_logger("agent.meeting_splitter")

UNASSIGNED = "__unassigned__"


def _segments_to_lite(segments: List[Dict[str, Any] | TranscriptSegment]) -> List[Dict[str, Any]]:
    """把 ASR transcript 规范成 LLM 友好的 dict 列表。"""
    lite = []
    for s in segments:
        if isinstance(s, TranscriptSegment):
            lite.append(s.model_dump())
        elif isinstance(s, dict):
            lite.append(
                {
                    "speaker": s.get("speaker") or s.get("speaker_id") or "SP00",
                    "start": float(s.get("start", 0.0)),
                    "end": float(s.get("end", 0.0)),
                    "text": s.get("text") or "",
                }
            )
    return lite


def _llm_split(
    candidates: List[MeetingCandidate],
    segments: List[Dict[str, Any]],
) -> MeetingSplitResult:
    """调 LLM 做语义切分,返回校验后的 MeetingSplitResult。"""
    # candidates 元数据无 PII(只有代号/诊断/部位);segments 的 text 可能有 → 脱敏
    combined_text = "\n".join(s.get("text", "") for s in segments)
    with scrub_session(combined_text) as sess:
        # 把脱敏后的文本按行回写到 segments(对齐顺序),保持 speaker/start/end 不变
        scrubbed_lines = sess.scrubbed.split("\n")
        scrubbed_segments: List[Dict[str, Any]] = []
        for i, s in enumerate(segments):
            new_s = dict(s)
            if i < len(scrubbed_lines):
                new_s["text"] = scrubbed_lines[i]
            scrubbed_segments.append(new_s)

        prompt = render(
            "meeting-split",
            candidates=[c.model_dump() for c in candidates],
            segments=scrubbed_segments,
        )
        result = chat_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 MDT 录音整理助手,严格按 schema 输出。"
                        "不允许伪造或改写 segment 内容;未讨论的患者必须 is_missing=true。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            schema=MeetingSplitResult,
            temperature=0.1,
            max_tokens=8000,
        )
        # 回填 PII placeholder → 原文(每个 split 的 segments[].text)
        restored_splits: List[MeetingSplit] = []
        for sp in result.splits:
            restored_segs: List[TranscriptSegment] = []
            for seg in sp.segments:
                restored_text = sess.restore(seg.text) if seg.text else seg.text
                restored_segs.append(
                    TranscriptSegment(
                        speaker=seg.speaker,
                        start=seg.start,
                        end=seg.end,
                        text=restored_text,
                    )
                )
            restored_splits.append(
                MeetingSplit(
                    session_id=sp.session_id,
                    patient_code=sp.patient_code,
                    is_missing=sp.is_missing,
                    segments=restored_segs,
                    confidence=sp.confidence,
                    evidence=sess.restore(sp.evidence) if sp.evidence else None,
                )
            )
        return MeetingSplitResult(splits=restored_splits)


def _enforce_mutex_and_align(
    candidates: List[MeetingCandidate],
    raw_segments: List[Dict[str, Any]],
    llm_result: MeetingSplitResult,
) -> Dict[str, List[TranscriptSegment]]:
    """红线后处理:
    1. 严禁同一 segment 归到多个 candidate(取第一次出现)
    2. 严禁改写 segment 内容(LLM 返回的 text/start/end 与原 segments 比对,以原为准)
    3. 严禁出现候选列表外的 session_id(兜底归到 __unassigned__)
    4. 漏归的 segment 落到 __unassigned__
    """
    valid_session_ids = {c.session_id for c in candidates} | {UNASSIGNED}

    # 用 (speaker, round(start, 2)) 作 key 匹配原 segment;ASR 时间戳通常稳定
    def _key(s: Dict[str, Any] | TranscriptSegment) -> tuple:
        if isinstance(s, TranscriptSegment):
            return (s.speaker, round(s.start, 2))
        return (s.get("speaker"), round(float(s.get("start", 0.0)), 2))

    raw_by_key: Dict[tuple, Dict[str, Any]] = {_key(s): s for s in raw_segments}
    used: set[tuple] = set()
    result: Dict[str, List[TranscriptSegment]] = {c.session_id: [] for c in candidates}
    result[UNASSIGNED] = []

    for split in llm_result.splits:
        tgt = split.session_id if split.session_id in valid_session_ids else UNASSIGNED
        for seg in split.segments:
            k = _key(seg)
            if k in used:
                continue  # 互斥:同一 segment 已归过
            if k not in raw_by_key:
                # LLM 编造了新 segment — 严禁,跳过
                logger.warning(
                    "meeting_splitter_forged_segment",
                    speaker=seg.speaker,
                    start=seg.start,
                    text_preview=seg.text[:40],
                )
                continue
            # 用原 segment(防止 LLM 改写 text)
            orig = raw_by_key[k]
            result[tgt].append(
                TranscriptSegment(
                    speaker=orig.get("speaker"),
                    start=float(orig.get("start", 0.0)),
                    end=float(orig.get("end", 0.0)),
                    text=orig.get("text", ""),
                )
            )
            used.add(k)

    # 漏归的 segments → __unassigned__
    for k, s in raw_by_key.items():
        if k in used:
            continue
        result[UNASSIGNED].append(
            TranscriptSegment(
                speaker=s.get("speaker"),
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=s.get("text", ""),
            )
        )

    return result


def run_meeting_splitter(
    segments: List[Dict[str, Any]] | List[TranscriptSegment],
    candidates: List[MeetingCandidate],
) -> Dict[str, Any]:
    """MVP 新功能核心:把一段多病例 MDT 录音按语义切分到各 session_id。

    返回:
    {
        "splits": {
            "<session_id_1>": [TranscriptSegment, ...],
            "<session_id_2>": [],          # is_missing
            "__unassigned__": [...]
        },
        "summary": [
            {"session_id": ..., "patient_code": ..., "is_missing": ...,
             "confidence": ..., "evidence": ..., "segment_count": ...}
        ]
    }

    LLM 失败时降级:第一个 candidate 接收全部 segments(便于人工修正),其余 is_missing=true。
    """
    if not candidates:
        logger.warning("meeting_splitter_no_candidates")
        return {"splits": {}, "summary": []}

    raw_segs = _segments_to_lite(segments)

    if not raw_segs:
        logger.info("meeting_splitter_no_segments")
        return {
            "splits": {c.session_id: [] for c in candidates} | {UNASSIGNED: []},
            "summary": [
                {
                    "session_id": c.session_id,
                    "patient_code": c.patient_code,
                    "is_missing": True,
                    "confidence": 0.0,
                    "evidence": None,
                    "segment_count": 0,
                }
                for c in candidates
            ],
        }

    # 单候选退化:直接全部归给唯一 candidate,跳过 LLM
    if len(candidates) == 1:
        only = candidates[0]
        return {
            "splits": {
                only.session_id: [
                    TranscriptSegment(**s) for s in raw_segs
                ],
                UNASSIGNED: [],
            },
            "summary": [
                {
                    "session_id": only.session_id,
                    "patient_code": only.patient_code,
                    "is_missing": False,
                    "confidence": 1.0,
                    "evidence": "单候选直接归属",
                    "segment_count": len(raw_segs),
                }
            ],
        }

    try:
        llm_result = _llm_split(candidates, raw_segs)
    except LLMError as e:
        logger.error("meeting_splitter_llm_failed", error=str(e))
        # 兜底:全部归到第一个 candidate,confidence=0,医生手动调整
        first = candidates[0]
        rest = candidates[1:]
        return {
            "splits": {
                first.session_id: [
                    TranscriptSegment(**s) for s in raw_segs
                ],
                **{c.session_id: [] for c in rest},
                UNASSIGNED: [],
            },
            "summary": [
                {
                    "session_id": first.session_id,
                    "patient_code": first.patient_code,
                    "is_missing": False,
                    "confidence": 0.0,
                    "evidence": "LLM 切分失败,全部归到第一位,请医生手动调整",
                    "segment_count": len(raw_segs),
                }
            ]
            + [
                {
                    "session_id": c.session_id,
                    "patient_code": c.patient_code,
                    "is_missing": True,
                    "confidence": 0.0,
                    "evidence": None,
                    "segment_count": 0,
                }
                for c in rest
            ],
        }

    # 红线后处理
    splits = _enforce_mutex_and_align(candidates, raw_segs, llm_result)

    # summary(用 LLM 给的 confidence/evidence,以 session_id 索引)
    llm_by_sid = {sp.session_id: sp for sp in llm_result.splits}
    summary = []
    for c in candidates:
        seg_list = splits.get(c.session_id, [])
        sp = llm_by_sid.get(c.session_id)
        summary.append(
            {
                "session_id": c.session_id,
                "patient_code": c.patient_code,
                "is_missing": (len(seg_list) == 0),
                "confidence": (sp.confidence if sp else 0.0),
                "evidence": (sp.evidence if sp else None),
                "segment_count": len(seg_list),
            }
        )

    return {"splits": splits, "summary": summary}
