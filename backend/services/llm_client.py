"""多 provider LLM 客户端 - OpenAI 兼容协议层。

设计要点:
- 所有 provider 走 OpenAI 兼容协议(/chat/completions),Claude 走 anthropic SDK 包装一层
- 通过 settings.llm_provider 路由;调用失败按 fallback_providers_list 顺序降级
- chat_json 强制 JSON 模式,Pydantic schema 校验失败自动 1 次重试(temperature=0)
- 文本/JSON 调用:调用方应在传入前用 pii_scrubber.scrub_session 脱敏
- 视觉(chat_vision_json):图片直接发送,无法脱敏像素 — 由 UX 引导医生拍照前遮挡 PII;
  返回的 raw_text 在交给下游 agent 前会被 scrub。
"""
from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from openai import APIError, OpenAI
from pydantic import BaseModel, ValidationError

from config import settings
from utils.logger import get_logger

logger = get_logger("llm_client")

T = TypeVar("T", bound=BaseModel)


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: Optional[str]
    model: str
    sdk: str = "openai"  # openai | anthropic


def _build_providers() -> Dict[str, ProviderConfig]:
    return {
        "doubao": ProviderConfig(
            name="doubao",
            base_url=settings.doubao_base_url,
            api_key=settings.doubao_api_key,
            model=settings.doubao_model,
        ),
        "qwen": ProviderConfig(
            name="qwen",
            base_url=settings.qwen_base_url,
            api_key=settings.qwen_api_key,
            model=settings.qwen_model,
        ),
        "kimi": ProviderConfig(
            name="kimi",
            base_url=settings.kimi_base_url,
            api_key=settings.kimi_api_key,
            model=settings.kimi_model,
        ),
        "claude": ProviderConfig(
            name="claude",
            base_url=settings.claude_base_url,
            api_key=settings.claude_api_key,
            model=settings.claude_model,
            sdk="anthropic",
        ),
        "gpt": ProviderConfig(
            name="gpt",
            base_url=settings.gpt_base_url,
            api_key=settings.gpt_api_key,
            model=settings.gpt_model,
        ),
    }


PROVIDERS = _build_providers()


class LLMError(Exception):
    """LLM 调用异常,封装底层错误。"""


class SchemaValidationError(LLMError):
    """LLM 输出 schema 校验失败。"""


def _select_chain(preferred: Optional[str] = None) -> List[ProviderConfig]:
    """根据偏好+全局 fallback 配置返回 provider 调用链。"""
    primary = preferred or settings.llm_provider
    chain_names: List[str] = [primary]
    for fb in settings.fallback_providers_list:
        if fb and fb not in chain_names:
            chain_names.append(fb)
    chain: List[ProviderConfig] = []
    for name in chain_names:
        cfg = PROVIDERS.get(name)
        if cfg is None:
            logger.warning("provider_unknown_skip", provider=name)
            continue
        if not cfg.api_key:
            logger.warning("provider_no_api_key_skip", provider=name)
            continue
        chain.append(cfg)
    return chain


def _openai_client(cfg: ProviderConfig) -> OpenAI:
    """生成 provider 对应的 OpenAI 兼容客户端。"""
    return OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=120.0)


def _call_openai_compat(
    cfg: ProviderConfig,
    messages: List[Dict[str, Any]],
    temperature: float,
    response_json: bool,
    max_tokens: Optional[int] = None,
) -> str:
    client = _openai_client(cfg)
    kwargs: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or ""
    return content


def _call_anthropic(
    cfg: ProviderConfig,
    messages: List[Dict[str, Any]],
    temperature: float,
    response_json: bool,
    max_tokens: Optional[int] = None,
) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise LLMError("anthropic SDK 未安装") from e

    client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url, timeout=120.0)
    system_msgs = [m["content"] for m in messages if m["role"] == "system"]
    other_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m["role"] in {"user", "assistant"}
    ]
    if response_json:
        if system_msgs:
            system_msgs.append(
                "你必须仅以一个合法 JSON 对象响应,不要包含任何 markdown 代码块或解释文字。"
            )
        else:
            system_msgs = [
                "你必须仅以一个合法 JSON 对象响应,不要包含任何 markdown 代码块或解释文字。"
            ]
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=max_tokens or 4096,
        system="\n\n".join(system_msgs) if system_msgs else None,
        messages=other_msgs,
        temperature=temperature,
    )
    parts = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


def _dispatch(
    cfg: ProviderConfig,
    messages: List[Dict[str, Any]],
    temperature: float,
    response_json: bool,
    max_tokens: Optional[int],
) -> str:
    if cfg.sdk == "anthropic":
        return _call_anthropic(cfg, messages, temperature, response_json, max_tokens)
    return _call_openai_compat(cfg, messages, temperature, response_json, max_tokens)


def chat_text(
    messages: List[Dict[str, Any]],
    *,
    preferred_provider: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
) -> str:
    """通用文本调用 - 不强制 JSON。失败按 fallback 链降级。"""
    chain = _select_chain(preferred_provider)
    if not chain:
        raise LLMError("no llm provider available - 请配置至少一个 API key")

    last_err: Optional[Exception] = None
    for cfg in chain:
        try:
            logger.info("llm_call_start", provider=cfg.name, model=cfg.model)
            text = _dispatch(cfg, messages, temperature, False, max_tokens)
            logger.info("llm_call_ok", provider=cfg.name, length=len(text))
            return text
        except (APIError, LLMError, Exception) as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "llm_call_failed_fallback",
                provider=cfg.name,
                error=type(e).__name__,
                detail=str(e)[:200],
            )
            continue

    raise LLMError(f"all providers failed: {last_err}") from last_err


def _strip_code_fence(text: str) -> str:
    """剥离 ```json ... ``` 包装(部分 provider 不支持 JSON mode 时会带)。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines:
            lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def chat_json(
    messages: List[Dict[str, Any]],
    schema: Type[T],
    *,
    preferred_provider: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    retry_on_validation: bool = True,
) -> T:
    """强制 JSON 模式,返回经 pydantic 校验后的实例。

    - schema 校验失败:第一次重试 temperature=0,并把错误原因塞回 system message
    - provider 失败:按 fallback 链降级
    """
    chain = _select_chain(preferred_provider)
    if not chain:
        raise LLMError("no llm provider available - 请配置至少一个 API key")

    last_err: Optional[Exception] = None
    for cfg in chain:
        attempt_messages = messages
        for attempt in range(2 if retry_on_validation else 1):
            try:
                logger.info(
                    "llm_json_start",
                    provider=cfg.name,
                    model=cfg.model,
                    attempt=attempt,
                )
                raw = _dispatch(
                    cfg,
                    attempt_messages,
                    temperature if attempt == 0 else 0.0,
                    True,
                    max_tokens,
                )
                payload_str = _strip_code_fence(raw)
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError as je:
                    raise SchemaValidationError(f"非合法 JSON: {je}; raw={raw[:300]}")
                try:
                    instance = schema.model_validate(payload)
                except ValidationError as ve:
                    raise SchemaValidationError(f"schema 校验失败: {ve}") from ve
                logger.info("llm_json_ok", provider=cfg.name, schema=schema.__name__)
                return instance
            except SchemaValidationError as sve:
                last_err = sve
                logger.warning(
                    "llm_json_validation_failed",
                    provider=cfg.name,
                    attempt=attempt,
                    detail=str(sve)[:200],
                )
                if attempt + 1 < 2 and retry_on_validation:
                    attempt_messages = list(messages) + [
                        {
                            "role": "system",
                            "content": (
                                f"上一次响应不符合 schema:\n{sve}\n"
                                f"请严格按照 {schema.__name__} 的字段和类型重新生成,"
                                "只返回 JSON 对象,无任何解释。"
                            ),
                        }
                    ]
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(
                    "llm_json_provider_failed",
                    provider=cfg.name,
                    error=type(e).__name__,
                    detail=str(e)[:200],
                )
                break  # 切下一个 provider
    raise LLMError(f"chat_json all attempts failed: {last_err}") from last_err


def build_image_content(
    images: List[Tuple[bytes, str]],
    text: str,
    *,
    text_first: bool = False,
) -> List[Dict[str, Any]]:
    """便利:把多张图片+文字组装成 OpenAI vision content blocks。

    images: [(bytes, mime), ...],例如 [(jpg_bytes, "image/jpeg"), (png_bytes, "image/png")]
    text: 紧跟图片的指令文本
    text_first: True 则把文本放在图片前(部分 provider 表现略好);默认图片在前更自然
    """
    image_blocks: List[Dict[str, Any]] = []
    for img_bytes, mime in images:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        image_blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    text_block = {"type": "text", "text": text}
    return [text_block, *image_blocks] if text_first else [*image_blocks, text_block]


def chat_vision_json(
    messages: List[Dict[str, Any]],
    schema: Type[T],
    *,
    preferred_provider: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    retry_on_validation: bool = True,
) -> T:
    """多模态视觉 + JSON 调用 — 适用于 OCR/影像识别等场景。

    messages 应包含 OpenAI 兼容的 vision content blocks,例如:
        [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
                {"type": "text", "text": "请提取..."},
            ],
        }]

    设计:
    - 仅走 OpenAI 兼容 provider(豆包/通义/Kimi/GPT);Claude 的 vision 走另一种 block 格式,
      MVP 阶段不混入 fallback,避免格式串台。如果 primary 是 claude,会跳过并尝试下一个。
    - 图片像素无法脱敏 — 由 UX 引导医生拍照前遮挡 PII;返回的 raw_text 交给调用方在下游入
      LLM 前再 scrub。
    - 校验失败 1 次重试;provider 失败按 fallback 链降级。
    """
    chain = _select_chain(preferred_provider)
    chain = [c for c in chain if c.sdk == "openai"]
    if not chain:
        raise LLMError(
            "no vision-capable provider available - 请配置豆包/通义/Kimi/GPT 中任一 API key"
        )

    last_err: Optional[Exception] = None
    for cfg in chain:
        attempt_messages = messages
        for attempt in range(2 if retry_on_validation else 1):
            try:
                logger.info(
                    "llm_vision_json_start",
                    provider=cfg.name,
                    model=cfg.model,
                    attempt=attempt,
                )
                raw = _call_openai_compat(
                    cfg,
                    attempt_messages,
                    temperature if attempt == 0 else 0.0,
                    True,
                    max_tokens,
                )
                payload_str = _strip_code_fence(raw)
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError as je:
                    raise SchemaValidationError(
                        f"非合法 JSON: {je}; raw={raw[:300]}"
                    )
                try:
                    instance = schema.model_validate(payload)
                except ValidationError as ve:
                    raise SchemaValidationError(f"schema 校验失败: {ve}") from ve
                logger.info(
                    "llm_vision_json_ok",
                    provider=cfg.name,
                    schema=schema.__name__,
                )
                return instance
            except SchemaValidationError as sve:
                last_err = sve
                logger.warning(
                    "llm_vision_validation_failed",
                    provider=cfg.name,
                    attempt=attempt,
                    detail=str(sve)[:200],
                )
                if attempt + 1 < 2 and retry_on_validation:
                    # 注入修复提示,保留原 image content blocks 不变
                    attempt_messages = [
                        {
                            "role": "system",
                            "content": (
                                f"上一次响应不符合 schema:\n{sve}\n"
                                f"请严格按照 {schema.__name__} 的字段和类型重新生成,"
                                "只返回 JSON 对象,无任何解释或 markdown。"
                            ),
                        }
                    ] + list(messages)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(
                    "llm_vision_provider_failed",
                    provider=cfg.name,
                    error=type(e).__name__,
                    detail=str(e)[:200],
                )
                break  # 切下一个 provider
    raise LLMError(
        f"chat_vision_json all attempts failed: {last_err}"
    ) from last_err


def is_provider_available(name: str) -> bool:
    cfg = PROVIDERS.get(name)
    return bool(cfg and cfg.api_key)


def healthcheck() -> Dict[str, bool]:
    """返回每个 provider 的可用性(只看 api_key,不真调)。"""
    return {name: bool(cfg.api_key) for name, cfg in PROVIDERS.items()}
