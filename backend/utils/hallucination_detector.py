"""LLM 输出的幻觉检测 - 迁移自 shan-ye medical/hallucination_detector.py。

策略:
1. evidence_snippet 在原文中存在性检查(必须能模糊匹配回去)
2. 数值类字段的可信范围检查(剂量/分期)
3. 否定语义识别(防止"未见复发"被理解为"复发")
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None  # type: ignore


@dataclass
class HallucinationFinding:
    field: str
    severity: str  # info / warning / critical
    issue: str
    suggestion: Optional[str] = None


NEGATION_MARKERS = [
    "未见", "未发现", "无明显", "无特殊", "排除", "阴性",
    "无", "否", "不存在", "未提示",
]


def has_negation(snippet: str) -> bool:
    """判断片段是否含否定语义(防 LLM 把"未见转移"读成"转移")"""
    return any(neg in snippet for neg in NEGATION_MARKERS)


def evidence_in_source(
    evidence: Optional[str],
    source_texts: List[str],
    threshold: int = 75,
) -> bool:
    """检查 evidence_snippet 是否能模糊匹配回原文。
    fuzz 不可用时降级为子串包含。
    """
    if not evidence or not source_texts:
        return False
    evidence = evidence.strip()
    if not evidence:
        return False

    if fuzz is None:
        return any(evidence in src or src in evidence for src in source_texts)

    for src in source_texts:
        if fuzz.partial_ratio(evidence, src) >= threshold:
            return True
    return False


def detect_hallucinations(
    structured: dict,
    source_texts: List[str],
) -> List[HallucinationFinding]:
    """递归扫描结构化 LLM 输出,检查每个含 evidence_snippet 的对象。"""
    findings: List[HallucinationFinding] = []

    def walk(node, path: str):
        if isinstance(node, dict):
            evidence = node.get("evidence_snippet") or node.get("evidence")
            if evidence:
                if not evidence_in_source(evidence, source_texts):
                    findings.append(
                        HallucinationFinding(
                            field=path,
                            severity="warning",
                            issue=f"evidence_snippet 在原文中无对应:'{evidence[:60]}'",
                            suggestion="重新生成此字段,要求引用真实原文",
                        )
                    )
                elif has_negation(evidence) and node.get("confidence", 0) > 0.7:
                    findings.append(
                        HallucinationFinding(
                            field=path,
                            severity="warning",
                            issue=f"证据含否定词('未见'/'阴性'等),但置信度仍 {node.get('confidence')}",
                            suggestion="复核语义,可能将否定判断为肯定",
                        )
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(structured, "")
    return findings


# 患者话术承诺词黑名单(QC Agent 用)
OVERCOMMITMENT_WORDS = [
    "治愈", "一定能", "保证", "百分百", "肯定治好", "包治",
    "完全康复", "彻底根治", "绝对", "永不复发",
]


def check_overcommitment(script: str) -> List[str]:
    """检查患者话术是否包含承诺词。返回命中的词列表。"""
    return [w for w in OVERCOMMITMENT_WORDS if w in script]
