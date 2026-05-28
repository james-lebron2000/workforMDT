"""PII 并发隔离测试 - 红线 R4 的硬性证明。

红线:`pii_scrubber` 的 mapping **必须**仅在单次 LLM 调用闭包内可见,
1000 次并发调用绝不串台。如果两个并发会话使用了同一个 _PlaceholderFactory,
会导致 patient A 的姓名被还原成 patient B 的姓名 — 这是绝对禁止的。

验证策略:
1. 构造 100 个唯一虚拟患者(姓名/手机/身份证/病历号 全部互不相同)
2. 用 ThreadPoolExecutor 起 50 个 worker,1000 次 scrub_session 并发跑
3. 每次跑:
   - scrub 后断言 patient 字段全部被替换成占位符
   - 模拟 LLM 把占位符原样返回(最坏情况)
   - restore 后断言还原值 = 原 patient 字段(不是其他患者的)
4. 任何一次还原值串台 → 测试失败 + 打印冲突 detail

跑法:
    cd /Users/lijinming/Documents/MDT
    PYTHONPATH=backend python -m pytest backend/tests/test_pii_concurrent.py -xvs
"""
from __future__ import annotations

import os
import random
import string
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from utils.pii_scrubber import (  # noqa: E402
    is_clean_for_llm,
    scrub_session,
)


# ---------- 生成虚拟患者(全部唯一) ----------

def _rand_name() -> str:
    chars = "王李张赵刘陈杨黄周吴徐孙马朱胡郭何高林郑罗梁谢宋唐许韩冯邓曹彭"
    given = "".join(random.choices("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰", k=2))
    return random.choice(chars) + given


def _rand_phone() -> str:
    return "1" + random.choice("3456789") + "".join(random.choices(string.digits, k=9))


def _rand_id_card() -> str:
    """符合 18 位身份证格式(校验位简化为随机)。"""
    return "".join(random.choices(string.digits, k=17)) + random.choice("0123456789X")


def _rand_mrn() -> str:
    return "M" + "".join(random.choices(string.digits, k=9))


def _make_patients(n: int) -> List[Dict[str, str]]:
    """生成 n 个全字段唯一的患者(防止偶然碰撞掩盖问题)。"""
    seen_names: set[str] = set()
    seen_phones: set[str] = set()
    seen_ids: set[str] = set()
    out: List[Dict[str, str]] = []
    while len(out) < n:
        p = {
            "name": _rand_name(),
            "phone": _rand_phone(),
            "id_card": _rand_id_card(),
            "mrn": _rand_mrn(),
        }
        if p["name"] in seen_names or p["phone"] in seen_phones or p["id_card"] in seen_ids:
            continue
        seen_names.add(p["name"])
        seen_phones.add(p["phone"])
        seen_ids.add(p["id_card"])
        out.append(p)
    return out


def _make_medical_record(p: Dict[str, str]) -> str:
    """构造一条含 PII 的虚拟病历文本。"""
    return (
        f"姓名:{p['name']}  联系电话:{p['phone']}\n"
        f"身份证:{p['id_card']}  病历号:{p['mrn']}\n"
        "主诉:咳嗽伴痰中带血 3 月\n"
        "现病史:患者于 2024-03 出现咳嗽,胸 CT 提示右肺上叶占位...\n"
    )


# ---------- 单次任务 ----------

def _one_round(patient: Dict[str, str]) -> Tuple[bool, str]:
    """对一个 patient 跑 scrub → 模拟 LLM → restore,断言无串台。

    返回 (success, detail)。
    """
    raw = _make_medical_record(patient)

    with scrub_session(raw) as sess:
        scrubbed = sess.scrubbed

        # ---- 断言 1:scrub 后原始 PII 全部消失 ----
        clean_ok, leak = is_clean_for_llm(scrubbed)
        if not clean_ok:
            return False, f"scrub 未清干净: {leak} | scrubbed head={scrubbed[:120]!r}"
        if patient["name"] in scrubbed:
            return False, f"姓名残留: {patient['name']} 还在 scrubbed 中"
        if patient["mrn"] in scrubbed:
            return False, f"病历号残留: {patient['mrn']}"

        # ---- 模拟 LLM 调用:把占位符放在结构化 JSON 字段里返回 ----
        fake_llm_output = {
            "summary": f"该患者({scrubbed.split(chr(10))[0].split(':')[1].split('  ')[0]})的初步评估...",
            "raw_excerpt": scrubbed[:200],
            "fields_with_placeholders": [
                scrubbed.split("\n")[0],
                scrubbed.split("\n")[1],
            ],
        }

        # ---- restore ----
        restored = sess.restore(fake_llm_output)

    # ---- 断言 2:restore 后能拿回**这个**患者的字段(不是别人的) ----
    restored_text = (
        restored["raw_excerpt"] + "\n" + "\n".join(restored["fields_with_placeholders"])
    )
    if patient["name"] not in restored_text:
        return False, f"restore 后丢失自己的姓名 {patient['name']}: {restored_text[:200]!r}"
    if patient["phone"] not in restored_text:
        return False, f"restore 后丢失自己的电话 {patient['phone']}"
    if patient["id_card"] not in restored_text:
        return False, f"restore 后丢失自己的身份证 {patient['id_card']}"
    if patient["mrn"] not in restored_text:
        return False, f"restore 后丢失自己的病历号 {patient['mrn']}"

    return True, ""


# ---------- 主测试 ----------

def test_pii_concurrent_1000_no_crosstalk():
    """1000 次并发 scrub/restore,验证 mapping 完全隔离,无任何字段串台。"""
    n_patients = 100
    n_rounds = 1000
    n_workers = 50

    patients = _make_patients(n_patients)

    # 每个 round 随机抽一个 patient(确保多次重复使用同一 patient)
    schedule = [random.choice(patients) for _ in range(n_rounds)]

    failures: List[str] = []
    cross_talk: List[str] = []

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        future_to_idx = {pool.submit(_one_round, p): i for i, p in enumerate(schedule)}
        for fut in as_completed(future_to_idx):
            ok, detail = fut.result()
            if not ok:
                failures.append(detail)

    assert not failures, (
        f"1000 并发中有 {len(failures)} 次失败,前 5 条:\n"
        + "\n".join(f"  - {d}" for d in failures[:5])
    )
    print(f"✅ {n_rounds} 并发 × {n_patients} 唯一患者 / {n_workers} workers 全部隔离,0 串台")


def test_pii_session_mapping_cleared_on_exit():
    """scrub_session 退出后 mapping 必须被清空 - 防止 finalizer 残留。"""
    raw = "姓名:张三  电话:13800138000"
    with scrub_session(raw) as sess:
        assert sess._mapping, "scrub 后 mapping 应该有内容"
        captured_mapping = sess._mapping
        snap = dict(captured_mapping)  # 复制一份用于事后断言
    # 出 with 之后,原 mapping 必须被清空
    assert captured_mapping == {}, (
        f"session.__exit__ 没清干净: 仍有 {captured_mapping} (原快照: {snap})"
    )


def test_pii_no_placeholder_collision_across_concurrent_sessions():
    """关键:两个并发 session 即使内容一致,placeholder 也是独立计数,
    不能因为 'mapping 全局共享' 而把 A 的 <NAME_1> 还原成 B 的姓名。
    """
    patient_a = {"name": "张三", "phone": "13800000001", "id_card": "110101199001011234", "mrn": "M100000001"}
    patient_b = {"name": "李四", "phone": "13900000002", "id_card": "110101199001021235", "mrn": "M100000002"}

    text_a = _make_medical_record(patient_a)
    text_b = _make_medical_record(patient_b)

    # 模拟并发:开两个 session,交错 restore
    sess_a = scrub_session.__new__(scrub_session)
    sess_a.__init__(text_a)
    sess_b = scrub_session.__new__(scrub_session)
    sess_b.__init__(text_b)

    # 拿 A 的占位符让 B 的 mapping 还原 - 必须**还不出来**或还成 B 自己的值
    fake_llm_with_a_placeholder = {"x": sess_a.scrubbed}
    restored_by_b = sess_b.restore(fake_llm_with_a_placeholder)

    # A 的 placeholder 在 B 的 mapping 里查不到,应保持占位符原样(不串台)
    # 关键断言:B 的 restore 绝不能把 <NAME_1> 还原成 "张三"
    assert "张三" not in restored_by_b["x"], (
        f"严重串台!B session 还原出了 A 的姓名: {restored_by_b['x'][:200]}"
    )

    # 各自 session restore 自己的输出应正常
    restored_a = sess_a.restore({"x": sess_a.scrubbed})
    restored_b = sess_b.restore({"x": sess_b.scrubbed})
    assert patient_a["name"] in restored_a["x"]
    assert patient_b["name"] in restored_b["x"]
    assert patient_a["name"] not in restored_b["x"], "A 名字漏到 B"
    assert patient_b["name"] not in restored_a["x"], "B 名字漏到 A"


if __name__ == "__main__":
    # 直接 python test_pii_concurrent.py 也能跑
    print("[1/3] test_pii_session_mapping_cleared_on_exit ...")
    test_pii_session_mapping_cleared_on_exit()
    print("  ✅")
    print("[2/3] test_pii_no_placeholder_collision_across_concurrent_sessions ...")
    test_pii_no_placeholder_collision_across_concurrent_sessions()
    print("  ✅")
    print("[3/3] test_pii_concurrent_1000_no_crosstalk ...")
    random.seed(42)
    test_pii_concurrent_1000_no_crosstalk()
    print("\n✅ 全部红线 R4 测试通过")
