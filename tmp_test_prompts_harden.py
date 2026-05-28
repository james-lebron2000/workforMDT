"""验证 case-summary / recommendation prompt 硬化效果(qc 是纯规则、无 LLM,跳过)。

对每个 agent:
- 跑 sample-2(HCC ypT3 复发多发转移)
- 看 stderr 中的 llm_json_validation_failed 次数(应为 0)
- 打印结果,人工核验字段完整 + 红线合规
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE = Path(
    "/Users/lijinming/Documents/Commerce/AItrial/treatbot_we/server/public/demo/sample-2-nsclc.jpg"
)
print(f"样本: {SAMPLE.name} ({SAMPLE.stat().st_size:,} bytes)\n")
img_bytes = SAMPLE.read_bytes()

# ---------- 把 stdlib logging 输出转发到一个 buffer,以便统计 retry ----------
LOG_BUF = io.StringIO()


class _BufHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            LOG_BUF.write(msg + "\n")
        except Exception:
            pass


_buf_handler = _BufHandler(level=logging.DEBUG)
_buf_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_buf_handler)
logging.getLogger().setLevel(logging.DEBUG)


def _count_retries(snapshot: str) -> tuple[int, int, int]:
    """从 buffer 中提取 (start, validation_failed, ok) 计数。"""
    n_start = snapshot.count('"event": "llm_json_start"') + snapshot.count(
        '"event":"llm_json_start"'
    )
    n_fail = snapshot.count('"event": "llm_json_validation_failed"') + snapshot.count(
        '"event":"llm_json_validation_failed"'
    )
    n_ok = snapshot.count('"event": "llm_json_ok"') + snapshot.count(
        '"event":"llm_json_ok"'
    )
    return n_start, n_fail, n_ok


# ========== Step 1: 先跑 OCR Vision 拿 raw_text 和 structured ==========
from agents.agent_01_ocr import run_ocr_vision_agent

t0 = time.time()
print("[Prep] OCR Vision Agent ...")
ocr = run_ocr_vision_agent([(img_bytes, "image/jpeg")])
print(f"  耗时 {time.time()-t0:.1f}s | raw_text {len(ocr['raw_text'])} 字")

raw_text = ocr["raw_text"]
structured = ocr["structured"]


# ========== Test 1: case_summary agent ==========
print("\n" + "=" * 60)
print("[Test 1] Case Summary Agent")
print("=" * 60)
LOG_BUF.seek(0)
LOG_BUF.truncate(0)
before_snapshot = LOG_BUF.getvalue()

from agents.agent_03_case_summary import run_case_summary_agent

t1 = time.time()
summary = run_case_summary_agent(
    ocr_texts=[raw_text],
    patient_request_text="希望明确肝癌术后多发转移的下一步治疗,腰部很痛,想看看能不能放疗止痛。",
    structured_records=[structured] if structured else [],
)
dt1 = time.time() - t1
print(f"\n  耗时 {dt1:.1f}s")
print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))

after = LOG_BUF.getvalue()
n_start, n_fail, n_ok = _count_retries(after[len(before_snapshot):])
print(f"\n  LLM 调用统计: start={n_start} validation_failed={n_fail} ok={n_ok}")
if n_start == 1 and n_fail == 0:
    print("  ✅ case_summary 一次过(无重试)")
else:
    print(f"  ⚠️ case_summary 有 {n_fail} 次重试")
    for line in after.split("\n"):
        if "validation_failed" in line:
            try:
                rec = json.loads(line)
                print(f"    detail: {rec.get('detail', '')[:300]}")
            except Exception:
                pass

# 红线核验
print("\n  🔒 红线核验:")
print(f"    ✅ chief_need 非空: {bool(summary.chief_need)} (长度 {len(summary.chief_need)})")
print(f"    ✅ history_summary 长度 {len(summary.history_summary)} / 2000 上限")
print(f"    ✅ treatment_timeline 数量: {len(summary.treatment_timeline)}")
print(f"    ✅ mdt_questions ≥1: {len(summary.mdt_questions) >= 1} (有 {len(summary.mdt_questions)} 条)")
print(f"    ✅ current_problem 非空: {bool(summary.current_problem)}")


# ========== Test 2: recommendation agent(用 case_summary + 假 TNM + 假 opinions) ==========
print("\n" + "=" * 60)
print("[Test 2] Recommendation Agent")
print("=" * 60)

# 跑 TNM 拿真实输入
from agents.agent_04_tnm import run_tnm_agent

t2_tnm = time.time()
tnm_obj = run_tnm_agent(ocr_texts=[raw_text], case_summary=structured)
print(f"  (准备步) TNM Agent 耗时 {time.time()-t2_tnm:.1f}s")

# 模拟 6 个科室意见(实际场景来自 ASR;这里直接构造)
fake_opinions = [
    {
        "department": "外科",
        "doctor_label": "外科甲",
        "opinion": "肝癌术后已 R0 切除,目前以系统治疗为主,外科暂无切除指征",
        "rationale": "T11-12 椎体转移为多发,非寡转移,不建议外科干预",
        "recommendation": "继续随访,出现脊髓压迫征象时考虑骨科介入",
        "evidence_source": "录音 12:30-15:10",
        "evidence_snippet": "目前看不是寡转移,外科没指征",
        "confidence": 0.85,
        "is_missing": False,
    },
    {
        "department": "肿瘤内科",
        "doctor_label": "内科甲",
        "opinion": "瑞戈非尼治疗中肺转移 PD,建议切换到三线方案如卡博替尼,或考虑临床试验",
        "rationale": "REACH-2 后肝癌后线证据,患者 PS 良好,可耐受",
        "recommendation": "切换卡博替尼或参与临床试验",
        "evidence_source": "录音 18:40-22:00",
        "evidence_snippet": "瑞戈非尼已经 PD 了,考虑卡博替尼",
        "confidence": 0.8,
        "is_missing": False,
    },
    {
        "department": "放射科",
        "doctor_label": "放射甲",
        "opinion": "PET 显示腰大肌及腰椎受累明确,既往腹膜后已放疗,需谨慎评估剂量限值",
        "rationale": "MR/PET 一致,二程放疗需脊髓 DVH 重叠核查",
        "recommendation": "完善脊柱 MRI 增强,评估二程放疗可行性",
        "evidence_source": "录音 23:10-25:00",
        "evidence_snippet": "腰大肌和腰椎都受累,前次放疗在腹膜后",
        "confidence": 0.9,
        "is_missing": False,
    },
    {
        "department": "放疗科",
        "doctor_label": "放疗甲",
        "opinion": "建议针对 T11-12 椎体及腰肌姑息减症放疗 30 Gy/10 次,二程剂量在限值内",
        "rationale": "症状明显,标准 30 Gy/10 次方案,脊髓 DVH 评估通过",
        "recommendation": "尽快开始姑息放疗",
        "evidence_source": "录音 26:20-30:00",
        "evidence_snippet": "30Gy 10 次,脊髓限值能控制住",
        "confidence": 0.9,
        "is_missing": False,
    },
    {
        "department": "介入科",
        "doctor_label": None,
        "opinion": None,
        "rationale": None,
        "recommendation": None,
        "evidence_source": None,
        "evidence_snippet": None,
        "confidence": 0.0,
        "is_missing": True,
    },
    {
        "department": "病理科",
        "doctor_label": None,
        "opinion": None,
        "rationale": None,
        "recommendation": None,
        "evidence_source": None,
        "evidence_snippet": None,
        "confidence": 0.0,
        "is_missing": True,
    },
]

LOG_BUF.seek(0)
LOG_BUF.truncate(0)
before_snapshot2 = LOG_BUF.getvalue()

from agents.agent_06_recommendation import run_recommendation_agent

t2 = time.time()
final = run_recommendation_agent(
    case_summary=summary.model_dump(),
    tnm=tnm_obj.model_dump(),
    opinions=fake_opinions,
)
dt2 = time.time() - t2
print(f"\n  耗时 {dt2:.1f}s")
print(json.dumps(final.model_dump(), ensure_ascii=False, indent=2))

after2 = LOG_BUF.getvalue()
n_start2, n_fail2, n_ok2 = _count_retries(after2[len(before_snapshot2):])
print(f"\n  LLM 调用统计: start={n_start2} validation_failed={n_fail2} ok={n_ok2}")
if n_start2 == 1 and n_fail2 == 0:
    print("  ✅ recommendation 一次过(无重试)")
else:
    print(f"  ⚠️ recommendation 有 {n_fail2} 次重试")
    for line in after2.split("\n"):
        if "validation_failed" in line:
            try:
                rec = json.loads(line)
                print(f"    detail: {rec.get('detail', '')[:300]}")
            except Exception:
                pass

# 红线核验
print("\n  🔒 红线核验:")
print(f"    ✅ clinical_judgment 长度 {len(final.clinical_judgment)} ≥ 10")
print(f"    ✅ tnm 复用输入: tnm_type={final.tnm.tnm_type} (input={tnm_obj.tnm_type})")
print(f"    ✅ suggested_exams 数量: {len(final.suggested_exams)}")
print(f"    ✅ treatment_plan 数量: {len(final.treatment_plan)}")
print(f"    ✅ 所有 treatment.needs_doctor_confirm=True: {all(t.needs_doctor_confirm for t in final.treatment_plan)}")
print(f"    ✅ referral 数量: {len(final.referral)} (含规则库补充)")
print(f"    ✅ patient_script 长度 {len(final.patient_script)} ∈ [20, 1500]")
forbidden = ["治愈", "一定能", "保证", "百分百", "肯定治好", "包治", "永不复发", "彻底根治"]
hit = [w for w in forbidden if w in final.patient_script]
print(f"    ✅ patient_script 无禁词: {not hit} {'(命中: ' + str(hit) + ')' if hit else ''}")


print("\n" + "=" * 60)
print("总结")
print("=" * 60)
total_retries = n_fail + n_fail2
if total_retries == 0:
    print(f"✅ 2 个 agent 共 {n_start + n_start2} 次 LLM 调用,0 重试。Prompt 硬化有效。")
else:
    print(f"⚠️ 仍有 {total_retries} 次重试,需进一步看 detail。")
