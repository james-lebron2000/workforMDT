"""Agent 02: ASR 后处理
- 输入: ASR 服务返回的 segments[] (含 speaker_id)
- 输出: 按 speaker 聚合的 chunks,便于下游归类
- 此 Agent 不直接调 LLM,只做声学层后处理
"""
from __future__ import annotations

from typing import Any, Dict, List


def aggregate_by_speaker(segments: List[Dict[str, Any]]) -> Dict[str, str]:
    """把同 speaker 的所有 text 拼成一段(保留时间标记),便于 LLM 整体判断科室。"""
    grouped: Dict[str, List[str]] = {}
    for seg in segments:
        sp = seg.get("speaker") or seg.get("speaker_id") or "未知"
        text = seg.get("text", "").strip()
        if not text:
            continue
        grouped.setdefault(sp, []).append(text)
    return {sp: " ".join(parts) for sp, parts in grouped.items()}


def stats(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    speakers = set()
    total = 0
    for seg in segments:
        sp = seg.get("speaker") or seg.get("speaker_id")
        if sp:
            speakers.add(sp)
        total += len(seg.get("text", ""))
    return {"speakers": sorted(speakers), "total_chars": total, "segments": len(segments)}
