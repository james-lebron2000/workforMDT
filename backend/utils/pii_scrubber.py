"""病历原文进 LLM 前的 PII 脱敏管道。

设计原则(从 treatbot piiScrubber.js 翻译):
- mapping 仅在内存中维护,从不写日志、不持久化、不发出业务响应。
- 生命周期严格限制在 `scrub_for_llm → LLM 调用 → restore_from_llm` 闭包内。
- 同次调用里同一原值复用同一占位符,LLM 在结构化输出中可安全引用。
- 占位符使用 `<TYPE_N>`(N 为 1-based 自增整数),便于正则定位回填。

覆盖类型:手机号 / 身份证 / 银行卡 / 邮箱 / 姓名(启发式) / 详细地址 / 病历号(MRN)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

# 中国大陆手机号
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 身份证 18 位
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
# 银行卡 16-19 位
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# 邮箱
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 姓名启发式:仅捕获「姓名:xxx」「患者:xxx」紧跟 2-4 字
_NAME_LABEL_RE = re.compile(r"(姓名|患者|病人)[\s ]*[::]\s*([一-龥A-Za-z·]{2,4})")
# 详细住址
_ADDRESS_RE = re.compile(
    r"[一-龥]{2,8}(?:省|自治区|特别行政区|市)"
    r"[一-龥A-Za-z0-9]{0,40}?(?:区|县|市)"
    r"[一-龥A-Za-z0-9]{0,40}?(?:路|街|道|巷|弄|号院?)\s*\d{0,6}号?"
    r"(?:[一-龥A-Za-z0-9]{0,20}?(?:号楼|单元|室|层))?"
)
# 病历号(医院 MRN,一般 6-12 位数字加可能字母前缀)
_MRN_RE = re.compile(r"(?:病历号|住院号|门诊号)[\s ]*[::]\s*([A-Za-z0-9]{6,16})")

_PLACEHOLDER_RE = re.compile(r"<(PHONE|ID|NAME|BANKCARD|EMAIL|ADDR|MRN)_\d+>")


@dataclass
class ScrubResult:
    scrubbed: str
    mapping: Dict[str, str] = field(default_factory=dict)


class _PlaceholderFactory:
    """每次 scrub_for_llm 调用独立一个实例,绝不共享。"""

    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._value_index: Dict[str, str] = {}  # f"{type}::{value}" → placeholder
        self.mapping: Dict[str, str] = {}  # placeholder → original_value

    def next(self, type_: str, value: str) -> str:
        key = f"{type_}::{value}"
        if key in self._value_index:
            return self._value_index[key]
        self._counters[type_] = self._counters.get(type_, 0) + 1
        placeholder = f"<{type_}_{self._counters[type_]}>"
        self._value_index[key] = placeholder
        self.mapping[placeholder] = value
        return placeholder


def scrub_for_llm(raw_text: str) -> ScrubResult:
    """对原始文本脱敏。返回的 mapping **必须**在 LLM 调用结束后立即丢弃,绝不入持久层。

    Note: 调用方应使用 `with` 风格的 `scrub_session` 上下文管理器以确保 mapping 自动释放。
    """
    if not raw_text:
        return ScrubResult(scrubbed="", mapping={})
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)

    factory = _PlaceholderFactory()
    text = raw_text

    # 顺序很重要:身份证 → 银行卡(避免 18 位身份证被当成银行卡)
    text = _ID_CARD_RE.sub(lambda m: factory.next("ID", m.group(0)), text)
    text = _PHONE_RE.sub(lambda m: factory.next("PHONE", m.group(0)), text)
    text = _BANK_CARD_RE.sub(lambda m: factory.next("BANKCARD", m.group(0)), text)
    text = _EMAIL_RE.sub(lambda m: factory.next("EMAIL", m.group(0)), text)
    text = _NAME_LABEL_RE.sub(
        lambda m: f"{m.group(1)}:{factory.next('NAME', m.group(2))}", text
    )
    text = _MRN_RE.sub(
        lambda m: f"病历号:{factory.next('MRN', m.group(1))}", text
    )
    text = _ADDRESS_RE.sub(lambda m: factory.next("ADDR", m.group(0)), text)

    return ScrubResult(scrubbed=text, mapping=factory.mapping)


def restore_from_llm(scrubbed_json: Any, mapping: Dict[str, str]) -> Any:
    """把 LLM 输出中残留的占位符按 mapping 回填。深拷贝替换。"""
    if not mapping or scrubbed_json is None:
        return scrubbed_json

    def restore_string(s: str) -> str:
        return _PLACEHOLDER_RE.sub(
            lambda m: mapping.get(m.group(0), m.group(0)), s
        )

    def walk(val: Any) -> Any:
        if isinstance(val, str):
            return restore_string(val)
        if isinstance(val, list):
            return [walk(v) for v in val]
        if isinstance(val, dict):
            return {k: walk(v) for k, v in val.items()}
        return val

    return walk(scrubbed_json)


class scrub_session:  # noqa: N801 — 像 contextmanager 一样用小写
    """上下文管理器形式,确保 mapping 出作用域立即销毁。

    用法:
        with scrub_session(raw_text) as session:
            llm_response = call_llm(session.scrubbed)
            return session.restore(llm_response)
        # mapping 已被清空
    """

    def __init__(self, raw_text: str):
        result = scrub_for_llm(raw_text)
        self.scrubbed = result.scrubbed
        self._mapping = result.mapping

    def __enter__(self) -> "scrub_session":
        return self

    def restore(self, scrubbed_json: Any) -> Any:
        return restore_from_llm(scrubbed_json, self._mapping)

    def __exit__(self, exc_type, exc, tb):
        self._mapping.clear()
        self._mapping = {}  # type: ignore
        return False


def is_clean_for_llm(text: str) -> Tuple[bool, list[str]]:
    """快速检查文本是否还有 PII 残留(用于单测)。"""
    issues = []
    if _PHONE_RE.search(text):
        issues.append("phone")
    if _ID_CARD_RE.search(text):
        issues.append("id_card")
    if _BANK_CARD_RE.search(text):
        issues.append("bank_card")
    if _EMAIL_RE.search(text):
        issues.append("email")
    return len(issues) == 0, issues
