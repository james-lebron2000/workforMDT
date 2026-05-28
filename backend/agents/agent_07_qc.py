"""Agent 07: QC 终检(关键安全防线)

三层检查:
1. 规则引擎(Python):承诺词、basis 占位符、is_missing 矛盾、剂量泄漏
2. 幻觉检测器(rapidfuzz):evidence_snippet 在原文中可定位
3. LLM 终检(可选):为更复杂的医学合理性判断兜底

输出: QCReport(passed, issues[], must_fix[])
"""
from __future__ import annotations

from typing import Any, Dict, List

from schemas.report import QCIssue, QCReport
from utils.dosage_guard import (
    qc_check_patient_script_dosage,
    qc_check_treatment_dosage_sanity,
)
from utils.hallucination_detector import (
    check_overcommitment,
    detect_hallucinations,
)
from utils.logger import get_logger

logger = get_logger("agent.qc")


def _rule_check_patient_script(script: str) -> List[QCIssue]:
    issues: List[QCIssue] = []
    if not script:
        issues.append(
            QCIssue(
                field="final.patient_script",
                severity="critical",
                issue="患者话术为空",
                suggestion="重新生成话术,200-600 字,通俗温和",
            )
        )
        return issues

    overcommit = check_overcommitment(script)
    if overcommit:
        issues.append(
            QCIssue(
                field="final.patient_script",
                severity="critical",
                issue=f"话术含承诺词:{'/'.join(overcommit)}",
                suggestion="替换为'有助于'/'可能'/'通常'等表述",
            )
        )
    # 剂量泄漏(委托 dosage_guard)
    for d in qc_check_patient_script_dosage(script):
        issues.append(QCIssue(**d))
    return issues


def _rule_check_tnm(tnm: Dict[str, Any]) -> List[QCIssue]:
    issues: List[QCIssue] = []
    basis = (tnm.get("basis") or "").strip()
    if not basis or basis.lower() in {"无", "unknown", "n/a", "待定", "未知"}:
        issues.append(
            QCIssue(
                field="tnm.basis",
                severity="critical",
                issue="TNM basis 缺失或为占位符",
                suggestion="必须引用病理/影像原文,≥10 字",
            )
        )
    conf = tnm.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf < 0.5:
        issues.append(
            QCIssue(
                field="tnm.confidence",
                severity="warning",
                issue=f"TNM 置信度低({conf})",
                suggestion="医生需手动复核分期或补充资料",
            )
        )
    return issues


def _rule_check_opinions(opinions: List[Dict[str, Any]]) -> List[QCIssue]:
    issues: List[QCIssue] = []
    for i, op in enumerate(opinions):
        is_missing = op.get("is_missing", False)
        opinion_text = op.get("opinion")
        if not is_missing and not opinion_text:
            issues.append(
                QCIssue(
                    field=f"opinions[{i}].opinion",
                    severity="critical",
                    issue=f"{op.get('department')} 标记非缺席但 opinion 为空",
                    suggestion="标 is_missing=true 或重新提炼意见",
                )
            )
    return issues


def _rule_check_treatments(treatments: List[Dict[str, Any]]) -> List[QCIssue]:
    issues: List[QCIssue] = []
    has_primary = any(t.get("kind") == "首选治疗" for t in treatments)
    if treatments and not has_primary:
        issues.append(
            QCIssue(
                field="final.treatment_plan",
                severity="info",
                issue="治疗建议中缺少'首选治疗'",
                suggestion="补充首选方案或明确标注本次不给首选",
            )
        )
    for i, t in enumerate(treatments):
        if t.get("needs_doctor_confirm") is False:
            issues.append(
                QCIssue(
                    field=f"final.treatment_plan[{i}].needs_doctor_confirm",
                    severity="critical",
                    issue="治疗建议未标'需医生最终确认'",
                    suggestion="所有治疗建议必须为 true",
                )
            )
    return issues


def run_qc_agent(
    case_summary: Dict[str, Any],
    tnm: Dict[str, Any],
    opinions: List[Dict[str, Any]],
    final: Dict[str, Any],
    source_texts: List[str],
) -> QCReport:
    """QC 终检 - 不调 LLM,纯规则。返回 QCReport。"""
    issues: List[QCIssue] = []

    # 1. 规则
    issues += _rule_check_patient_script(final.get("patient_script", ""))
    issues += _rule_check_tnm(tnm)
    issues += _rule_check_opinions(opinions)
    issues += _rule_check_treatments(final.get("treatment_plan", []))
    # 治疗方案剂量合理性(warning 级)
    for d in qc_check_treatment_dosage_sanity(final.get("treatment_plan", [])):
        issues.append(QCIssue(**d))

    # 2. 幻觉检测器(evidence_snippet vs 原文)
    bundle = {
        "case_summary": case_summary,
        "opinions": opinions,
        "final": final,
        "tnm": tnm,
    }
    hallucination_findings = detect_hallucinations(bundle, source_texts)
    for h in hallucination_findings:
        issues.append(
            QCIssue(
                field=h.field,
                severity=h.severity,  # type: ignore[arg-type]
                issue=h.issue,
                suggestion=h.suggestion,
            )
        )

    must_fix = [i.field for i in issues if i.severity == "critical"]
    passed = len(must_fix) == 0

    logger.info(
        "qc_done",
        total=len(issues),
        critical=len(must_fix),
        passed=passed,
    )
    return QCReport(passed=passed, issues=issues, must_fix=must_fix)
