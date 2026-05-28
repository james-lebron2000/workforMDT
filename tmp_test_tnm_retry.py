"""验证 TNM prompt 修复:第 1 次调用就应通过 schema,不再重试。

对照基线(prompt 改前):
  attempt 0 → schema 校验失败(LLM 用了 type/T/N/M 短字段名)
  attempt 1 → 成功
  总耗时 ~85s

期望(prompt 改后):
  attempt 0 → 直接成功
  总耗时 ~40-45s
  no `llm_json_validation_failed` warning in logs
"""
from __future__ import annotations

import io
import json
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE = Path(
    "/Users/lijinming/Documents/Commerce/AItrial/treatbot_we/server/public/demo/sample-2-nsclc.jpg"
)
print(f"样本: {SAMPLE.name} ({SAMPLE.stat().st_size:,} bytes)")

img_bytes = SAMPLE.read_bytes()

# 捕获 stderr 来统计 retry 次数
buf = io.StringIO()


def run():
    from agents.agent_01_ocr import run_ocr_vision_agent
    from agents.agent_04_tnm import run_tnm_agent

    # ---- Step 1: OCR Vision ----
    t0 = time.time()
    print("\n[Step 1/2] OCR Vision Agent ...")
    ocr = run_ocr_vision_agent([(img_bytes, "image/jpeg")])
    dt1 = time.time() - t0
    raw_text = ocr["raw_text"]
    structured = ocr["structured"]
    print(f"  耗时 {dt1:.1f}s | raw_text {len(raw_text)} 字 | stage={structured.get('stage')}")

    # ---- Step 2: TNM Agent ----
    print("\n[Step 2/2] TNM Agent ...")
    t1 = time.time()
    tnm = run_tnm_agent(ocr_texts=[raw_text], case_summary=structured)
    dt2 = time.time() - t1
    print(f"  耗时 {dt2:.1f}s")

    print("\n" + "=" * 60)
    print("TNM 输出:")
    print("=" * 60)
    print(json.dumps(tnm.model_dump(), ensure_ascii=False, indent=2))
    return dt1, dt2


# 同时把日志(走 stderr)收下来,以便统计 attempt 次数
with redirect_stderr(buf):
    dt1, dt2 = run()

logs = buf.getvalue()
# stderr 里的 json 日志行;统计 llm_json_start / llm_json_ok / llm_json_validation_failed
n_attempts = logs.count('"event": "llm_json_start"') + logs.count(
    '"event":"llm_json_start"'
)
n_validation_failed = logs.count('"event": "llm_json_validation_failed"') + logs.count(
    '"event":"llm_json_validation_failed"'
)
n_ok = logs.count('"event": "llm_json_ok"') + logs.count('"event":"llm_json_ok"')

print("\n" + "=" * 60)
print("LLM 调用统计(TNM agent 部分):")
print("=" * 60)
# 减去 OCR vision 那次调用的统计(那是 llm_vision_json_*,不是 llm_json_*)
# 所以 n_attempts / n_validation_failed / n_ok 应该都只来自 TNM agent
print(f"  llm_json_start 次数:        {n_attempts}")
print(f"  llm_json_validation_failed:  {n_validation_failed}")
print(f"  llm_json_ok:                 {n_ok}")
if n_attempts == 1 and n_validation_failed == 0:
    print("  ✅ TNM 一次过(无重试)— prompt 修复成功")
elif n_attempts == 2 and n_validation_failed == 1:
    print("  ⚠️ 仍有 1 次重试 — 看 stderr 找新的字段名错误")
else:
    print(f"  ❓ 调用次数 {n_attempts},重试 {n_validation_failed} 次")

# 把含 validation_failed 的日志行打出来,方便排错
if n_validation_failed > 0:
    print("\n--- validation_failed 日志(便于排错) ---")
    for line in logs.split("\n"):
        if "validation_failed" in line:
            # detail 字段会包含具体哪个 field 错了
            try:
                rec = json.loads(line)
                print(f"  attempt={rec.get('attempt')} detail={rec.get('detail', '')[:300]}")
            except Exception:
                print(f"  {line[:300]}")
