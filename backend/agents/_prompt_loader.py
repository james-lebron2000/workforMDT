"""Prompt 模板加载 + 简易变量替换。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "v1"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """读取 prompts/v1/<name>.md 内容。"""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def render(name: str, **variables: Any) -> str:
    """把 prompt 中的 {var} 占位替换为传入的变量。
    复杂结构走 json.dumps(ensure_ascii=False, indent=2)。
    """
    tpl = load_prompt(name)
    formatted: dict[str, str] = {}
    for k, v in variables.items():
        if isinstance(v, str):
            formatted[k] = v
        elif v is None:
            formatted[k] = ""
        else:
            formatted[k] = json.dumps(v, ensure_ascii=False, indent=2, default=str)
    # 用简单的 replace 而不是 format,避免 prompt 中花括号(JSON 例子)被误解析
    out = tpl
    for k, v in formatted.items():
        out = out.replace(f"{{{k}}}", v)
    return out
