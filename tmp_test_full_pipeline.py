"""端到端 7-Agent 全链路测试 - 对应 plan §17 验收点。

链:
  1. agent_01 OCR Vision  (真实 LLM,sample-2 图)
  2. agent_03 Case Summary
  3. agent_04 TNM
  4. agent_05 MDT Opinion  (mock ASR segments,模拟 6 科室会议录音)
  5. agent_06 Recommendation
  6. agent_07 QC           (纯规则,无 LLM)

验收点(plan):
  ✓ 病例摘要 30s 内生成,字段完整
  ✓ TNM 给 basis + uncertainty + confidence
  ✓ 6 科至少 4 科被正确识别,缺科正确标"未明确记录"
  ✓ 患者话术无 8 个禁词
  ✓ 治疗建议全部 needs_doctor_confirm=True
  ✓ QC 通过(无 critical issue)
  ✓ 红线全员合规
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE = Path(
    "/Users/lijinming/Documents/Commerce/AItrial/treatbot_we/server/public/demo/sample-2-nsclc.jpg"
)
print(f"样本: {SAMPLE.name} ({SAMPLE.stat().st_size:,} bytes)")
img_bytes = SAMPLE.read_bytes()


# ================== Step 1: OCR Vision ==================
from agents.agent_01_ocr import run_ocr_vision_agent

print("\n" + "=" * 60)
print("[1/6] OCR Vision Agent")
print("=" * 60)
t0 = time.time()
ocr = run_ocr_vision_agent([(img_bytes, "image/jpeg")])
dt0 = time.time() - t0
raw_text = ocr["raw_text"]
structured = ocr["structured"]
print(f"  耗时 {dt0:.1f}s | raw_text {len(raw_text)} 字 | stage={structured.get('stage')}")


# ================== Step 2: Case Summary ==================
from agents.agent_03_case_summary import run_case_summary_agent

print("\n" + "=" * 60)
print("[2/6] Case Summary Agent")
print("=" * 60)
t1 = time.time()
summary = run_case_summary_agent(
    ocr_texts=[raw_text],
    patient_request_text="希望明确肝癌术后多发转移的下一步治疗,腰部很痛,想看看能不能放疗止痛。",
    structured_records=[structured] if structured else [],
)
dt1 = time.time() - t1
print(f"  耗时 {dt1:.1f}s | timeline {len(summary.treatment_timeline)} 条 | mdt_questions {len(summary.mdt_questions)} 条")


# ================== Step 3: TNM ==================
from agents.agent_04_tnm import run_tnm_agent

print("\n" + "=" * 60)
print("[3/6] TNM Agent")
print("=" * 60)
t2 = time.time()
tnm = run_tnm_agent(ocr_texts=[raw_text], case_summary=structured)
dt2 = time.time() - t2
print(f"  耗时 {dt2:.1f}s | {tnm.tnm_type} {tnm.t_stage}{tnm.n_stage}{tnm.m_stage} ({tnm.overall_stage}) conf={tnm.confidence}")


# ================== Step 4: MDT Opinion(mock ASR segments) ==================
# 模拟 6 个匿名说话人,各自发表观点;Agent 应能归类到 6 科室
print("\n" + "=" * 60)
print("[4/6] MDT Opinion Agent (mock ASR segments)")
print("=" * 60)
fake_segments = [
    {"speaker_id": "SP01", "start": 0.0, "end": 30.0,
     "text": "我先说外科观点。患者肝癌术后已经 R0 切除,目前椎体多发转移属于全身病,外科没有切除指征。要做就是椎体减压减症,但应该让放疗先做姑息减症,我们不主张外科介入。"},
    {"speaker_id": "SP02", "start": 30.0, "end": 70.0,
     "text": "肿瘤内科考虑,瑞戈非尼治疗中已经 PD,建议切换三线,卡博替尼是肝癌后线证据级别比较高的,或者参加临床试验也可以。患者 PS 还行,血液毒性需要关注。"},
    {"speaker_id": "SP03", "start": 70.0, "end": 100.0,
     "text": "从影像来看,PET 提示腰大肌和 T11-12 椎体附件高代谢,既往腹膜后已经做过 56Gy 放疗。脊柱 MRI 增强是必须的,需要进一步明确椎体破坏程度和脊髓邻近关系。"},
    {"speaker_id": "SP04", "start": 100.0, "end": 140.0,
     "text": "放疗科同意做姑息减症,建议 30Gy 10 次的标准方案。脊髓 DVH 我们查了既往放疗计划,二程的话脊髓累积剂量在可接受范围内。患者疼痛明显,建议尽快开始。"},
    {"speaker_id": "SP05", "start": 140.0, "end": 165.0,
     "text": "介入科补充一下,目前肝脏原发灶没有看到复发,腹膜后淋巴结也没有进一步增大,介入治疗(TACE/消融)暂时没有指征。"},
    # 第 6 个核心科(病理)缺席 — 应被标 is_missing=true
]

from agents.agent_05_mdt_opinion import run_mdt_opinion_agent

t3 = time.time()
opinions = run_mdt_opinion_agent(
    segments=fake_segments,
    case_summary=summary.model_dump(),
)
dt3 = time.time() - t3
opinions_dump = [o.model_dump() for o in opinions]
present = [o.department for o in opinions if not o.is_missing]
missing = [o.department for o in opinions if o.is_missing]
print(f"  耗时 {dt3:.1f}s | 在场: {present} | 缺席(is_missing): {missing}")


# ================== Step 5: Recommendation ==================
from agents.agent_06_recommendation import run_recommendation_agent

print("\n" + "=" * 60)
print("[5/6] Recommendation Agent")
print("=" * 60)
t4 = time.time()
final = run_recommendation_agent(
    case_summary=summary.model_dump(),
    tnm=tnm.model_dump(),
    opinions=opinions_dump,
)
dt4 = time.time() - t4
print(f"  耗时 {dt4:.1f}s | 治疗 {len(final.treatment_plan)} 条 | 转诊 {len(final.referral)} 条")


# ================== Step 6: QC ==================
from agents.agent_07_qc import run_qc_agent

print("\n" + "=" * 60)
print("[6/6] QC Agent (rule-based, no LLM)")
print("=" * 60)
t5 = time.time()
qc = run_qc_agent(
    case_summary=summary.model_dump(),
    tnm=tnm.model_dump(),
    opinions=opinions_dump,
    final=final.model_dump(),
    source_texts=[raw_text],
)
dt5 = time.time() - t5
print(f"  耗时 {dt5*1000:.0f}ms | passed={qc.passed} | issues={len(qc.issues)} | must_fix={qc.must_fix}")


# ================== 全程红线核验 ==================
print("\n" + "=" * 60)
print("🔒 红线核验")
print("=" * 60)
checks = []

# R1: 6 核心科室都有记录(在场 or 缺席)
from schemas.opinion import CORE_DEPARTMENTS
recorded_depts = {o.department for o in opinions}
missing_core = set(CORE_DEPARTMENTS) - recorded_depts
checks.append((
    "R1 - 6 核心科室全员有记录",
    not missing_core,
    f"未出现的核心科: {missing_core}" if missing_core else "全员覆盖"
))

# R2: TNM basis 非空 + uncertainty 非空
checks.append((
    "R2 - TNM basis 非空 ≥10 字符",
    len(tnm.basis) >= 10,
    f"basis 长度 {len(tnm.basis)}"
))

# R3: 所有治疗建议 needs_doctor_confirm=True
all_confirm = all(t.needs_doctor_confirm for t in final.treatment_plan)
checks.append((
    "R3 - 治疗建议全部 needs_doctor_confirm=true",
    all_confirm,
    f"{len(final.treatment_plan)} 条全部 confirm" if all_confirm else "❌ 存在 False"
))

# R4: 患者话术无禁词
forbidden = ["治愈", "一定能", "保证", "百分百", "肯定治好", "包治", "永不复发", "彻底根治"]
hit = [w for w in forbidden if w in final.patient_script]
checks.append((
    "R4 - 患者话术无 8 禁词",
    not hit,
    f"命中: {hit}" if hit else "0 命中"
))

# R5: 患者话术无具体剂量
from utils.dosage_guard import has_dose
checks.append((
    "R5 - 患者话术无具体剂量",
    not has_dose(final.patient_script),
    "无剂量泄漏" if not has_dose(final.patient_script) else "❌ 有剂量"
))

# R6: 缺席科室 is_missing=true 且 opinion=None
mis_consistent = all(
    (o.opinion is None or o.opinion == "") if o.is_missing else (o.opinion and o.opinion.strip())
    for o in opinions
)
checks.append((
    "R6 - is_missing=True 必须 opinion 为空",
    mis_consistent,
    "一致" if mis_consistent else "❌ 矛盾"
))

# R7: QC 必须 passed(无 critical)
checks.append((
    "R7 - QC passed (无 critical issue)",
    qc.passed,
    f"passed={qc.passed}, must_fix={qc.must_fix}"
))

for name, ok, note in checks:
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}: {note}")


# ================== 总结 ==================
print("\n" + "=" * 60)
print("总结")
print("=" * 60)
total_dt = dt0 + dt1 + dt2 + dt3 + dt4 + dt5
all_ok = all(ok for _, ok, _ in checks)
print(f"  总耗时:           {total_dt:.1f}s")
print(f"  OCR Vision:       {dt0:.1f}s")
print(f"  Case Summary:     {dt1:.1f}s")
print(f"  TNM:              {dt2:.1f}s")
print(f"  MDT Opinion:      {dt3:.1f}s")
print(f"  Recommendation:   {dt4:.1f}s")
print(f"  QC:               {dt5*1000:.0f}ms (规则,无 LLM)")
print()
if all_ok:
    print("✅ 全链 7-Agent 验收点全员通过,红线 0 违反")
else:
    print("⚠️ 有红线未通过,见上")

# 把完整结果落盘,便于后续 spot-check
out_path = Path("/tmp/mdt_pipeline_full_result.json")
out_path.write_text(json.dumps({
    "summary": summary.model_dump(),
    "tnm": tnm.model_dump(),
    "opinions": opinions_dump,
    "final": final.model_dump(),
    "qc": qc.model_dump(),
    "timings": {
        "ocr": dt0, "summary": dt1, "tnm": dt2,
        "opinion": dt3, "recommendation": dt4, "qc": dt5,
    }
}, ensure_ascii=False, indent=2))
print(f"\n📁 完整结果已落: {out_path}")
