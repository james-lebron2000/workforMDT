"""剂量识别与异常检测 - 红线"患者话术不带具体剂量"的兜底。

来源:从 shan-ye-medical-ai/src/shanye/medical/dosage_guard.py 借鉴。
设计原则:
- 检测,不修复:发现问题报警,不擅自删改医生输出
- 只识别"具体数字 + 单位"组合,忽略纯文字描述("低剂量"/"标准剂量")
- 患者话术里**任何**剂量都报 critical;医生治疗建议里的剂量是合法的

被以下模块复用:
- agent_07_qc:扫描 patient_script 是否含剂量泄漏
- agent_06_recommendation:LLM 输出后 patient_script 兜底扫描
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

# 完整剂量正则:数字 + 可选小数 + 可选空格 + 常见医学单位
# 命中:
#   "贝伐 5mg/kg"、"卡铂 AUC5"、"56 Gy/28 次"、"100 mg/m2/d"、"800 U/m2"、"1.5 mCi"
# 不命中:
#   "白细胞 1.96"(没有标准单位)、"6 周期"、"III 级"
_DOSE_PATTERN = (
    r"\b\d+(?:\.\d+)?"
    r"(?:\s*(?:mg|g|μg|ug|kg|mL|ml|L|U|IU|mCi|cGy|Gy|Bq|×10\^9|x10\^9)"
    r"(?:/m2|/m²|/kg|/d|/天|/次|/周|/cycle|/c)?)+"
)
DOSE_RE = re.compile(_DOSE_PATTERN, re.IGNORECASE)

# AUC 是肿瘤化疗里特有的剂量表达,单独识别
_AUC_RE = re.compile(r"\bAUC\s*\d+(?:\.\d+)?\b", re.IGNORECASE)


@dataclass
class DoseHit:
    text: str               # 命中片段,如 "56Gy/28次"
    start: int              # 在原文中的起始位置
    end: int
    kind: str               # "dose" / "auc"


def find_doses(text: str) -> List[DoseHit]:
    """扫描文本里所有剂量表达。"""
    if not text:
        return []
    hits: List[DoseHit] = []
    for m in DOSE_RE.finditer(text):
        hits.append(DoseHit(text=m.group(0), start=m.start(), end=m.end(), kind="dose"))
    for m in _AUC_RE.finditer(text):
        hits.append(DoseHit(text=m.group(0), start=m.start(), end=m.end(), kind="auc"))
    hits.sort(key=lambda h: h.start)
    return hits


def has_dose(text: str) -> bool:
    """快速判断文本中是否含剂量。"""
    if not text:
        return False
    return bool(DOSE_RE.search(text) or _AUC_RE.search(text))


def scan_patient_script(script: str) -> List[Tuple[str, str]]:
    """专门给 patient_script 用 - 任何命中都是 critical。

    返回 (snippet, suggestion) 列表。给 QC agent 喂的格式。
    """
    if not script:
        return []
    hits = find_doses(script)
    out: List[Tuple[str, str]] = []
    for h in hits:
        ctx_start = max(0, h.start - 15)
        ctx_end = min(len(script), h.end + 15)
        snippet = script[ctx_start:ctx_end]
        suggestion = (
            f"患者话术中含具体剂量 '{h.text}'(上下文:'...{snippet}...')— "
            "请改为'按医嘱使用'/'打针'/'口服药物'等抽象表述,具体剂量不应面向患者。"
        )
        out.append((h.text, suggestion))
    return out


def redact_for_patient_script(script: str, *, placeholder: str = "[按医嘱]") -> str:
    """**辅助工具**:把患者话术中的剂量替换成占位符。不替换 AUC(那是治疗术语,医生看)。

    注意:本函数仅做提示用,**不应**自动用于覆盖 LLM 输出 —
    QC 应该报警让 LLM 重生成,而不是悄悄改写。
    """
    if not script:
        return script
    return DOSE_RE.sub(placeholder, script)


def check_treatment_regimen_sanity(
    regimen: str,
) -> List[str]:
    """治疗建议 regimen 里的剂量做粗略合理性检查。

    返回告警列表(空 = ok)。
    这里只做"明显异常"判断,不做药物 vs 剂量的精确知识库匹配
    (那需要 SNOMED CT / RxNorm 级别字典,超出 MVP 范围)。
    """
    warnings: List[str] = []
    if not regimen:
        return warnings

    # 放疗剂量:单次 > 30 Gy 异常高,总量 > 150 Gy 异常高
    gy_matches = re.findall(r"(\d+(?:\.\d+)?)\s*Gy(?:/(\d+))?", regimen, re.IGNORECASE)
    for m in gy_matches:
        try:
            total = float(m[0])
            fractions = int(m[1]) if m[1] else 1
            per_fx = total / max(fractions, 1)
            if total > 150:
                warnings.append(
                    f"放疗总剂量 {total}Gy 异常高(常见 ≤ 80Gy),请医生复核"
                )
            if per_fx > 30:
                warnings.append(
                    f"放疗单次剂量 {per_fx:.1f}Gy 异常高(SBRT 上限 ~24Gy),请医生复核"
                )
        except (ValueError, IndexError):
            pass

    # mg/kg 单次 > 50 异常
    mgkg_matches = re.findall(r"(\d+(?:\.\d+)?)\s*mg/kg", regimen, re.IGNORECASE)
    for m in mgkg_matches:
        try:
            if float(m) > 50:
                warnings.append(f"剂量 {m}mg/kg 异常高,请医生复核")
        except ValueError:
            pass

    return warnings


# ---------- 给 agent_07_qc 用的便捷出口(避免 qc 自己写正则) ----------

def qc_check_patient_script_dosage(script: str) -> List[dict]:
    """返回 QCIssue 友好格式的字典列表(severity=critical)。"""
    issues: List[dict] = []
    for snippet, suggestion in scan_patient_script(script):
        issues.append({
            "field": "final.patient_script",
            "severity": "critical",
            "issue": f"话术中含具体剂量 '{snippet}',应面向患者去掉",
            "suggestion": suggestion,
        })
    return issues


def qc_check_treatment_dosage_sanity(treatments: List[dict]) -> List[dict]:
    """治疗方案 regimen 字段的粗略合理性检查,warning 级别。"""
    issues: List[dict] = []
    for i, t in enumerate(treatments or []):
        regimen = t.get("regimen") or ""
        for warn in check_treatment_regimen_sanity(regimen):
            issues.append({
                "field": f"final.treatment_plan[{i}].regimen",
                "severity": "warning",
                "issue": warn,
                "suggestion": "对照指南或既往疗效复核剂量",
            })
    return issues
