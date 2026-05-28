"""火山引擎音频理解(豆包音频模型)— 替代自部署 FunASR。

参考文档:https://www.volcengine.com/docs/82379/2377589

调用方式:OpenAI 兼容 /chat/completions,把音频以 base64 形式塞进 content blocks。
返回值:与 infer_client.asr_transcribe 兼容的 `{segments, num_speakers, duration}` 结构,
        celery_tasks.asr_task 不需要改对接面。

⚠️ 红线说明
------------
1. 原音频(原始 bytes)会发送到火山自有云。这与 V1 时期"原音频不出本服务器"已经不一致,
   docs/privacy-policy.md §2 / §3 已同步更新,同意书 REQUIRED_AFFIRMATIONS 也已包含此条。
2. PII 脱敏:音频本身无法脱敏,转写返回的 text 在交给下游 LLM 前必须经
   utils.pii_scrubber.scrub_session。
3. 不向第三方分享、不用于模型训练 — 与火山引擎企业协议要求一致,合规由商务条款兜底。

字段约定(已对照 https://www.volcengine.com/docs/82379/2377589 校对)
-------------------------------------------------------------------
- content block:{"type": "audio", "audio": {"data": <base64>, "format": "mp3" | "wav"}}
- 模型 id:DOUBAO_AUDIO_MODEL,默认 doubao-seed-2.0-lite(全模态理解模型)
- endpoint:与文本模型同走 /chat/completions(豆包方舟,不是 /audio/transcriptions)

待观察
------
- 说话人分离:目前依赖模型在 prompt 里给出 speaker_id;若官方推出原生 diarization 字段,
  只需扩展 _parse_response 即可,不影响下游。
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from config import settings
from utils.logger import get_logger

logger = get_logger("volcengine_audio")


class VolcAudioError(Exception):
    """火山音频调用异常。"""


# ---- 内部:OpenAI 兼容客户端复用豆包配置 ----


def _client() -> OpenAI:
    if not settings.doubao_api_key:
        raise VolcAudioError(
            "doubao_api_key 未配置 — 设 ASR_PROVIDER=funasr 或填 DOUBAO_API_KEY"
        )
    return OpenAI(
        api_key=settings.doubao_api_key,
        base_url=settings.doubao_base_url,
        timeout=300.0,  # 30 分钟会议音频要留足上传 + 推理时间
    )


def _audio_mime_from_filename(filename: str) -> str:
    """根据文件后缀推断 mime;失败默认 audio/mpeg。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
    }.get(ext, "audio/mpeg")


def _audio_format_token(filename: str) -> str:
    """火山方舟 audio.format 字段值,官方仅支持 mp3 / wav,不识别其他后缀时降级 mp3。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if ext in {"mp3", "wav"} else "mp3"


def _build_messages(
    audio_b64: str,
    audio_format: str,
    voice_type: str,
    hotwords: List[str],
    enable_diarization: bool,
) -> List[Dict[str, Any]]:
    """组装 OpenAI 兼容的 multimodal messages。

    系统提示明确要求 JSON 输出,并指定字段格式,与 infer_client.asr_transcribe
    返回结构保持一致。
    """
    hotword_text = "、".join(hotwords[:200]) if hotwords else "(无)"

    system_prompt = (
        "你是医学多学科会议(MDT)录音转写助手。任务:\n"
        "1) 将输入音频逐句转写为中文文本,标点正确,医学术语优先使用专业词;\n"
        "2) 输出 JSON 数组 segments,每段含 speaker_id(字符串)/start(秒,float)/"
        "end(秒,float)/text(string)。\n"
        "3) 如音频中有多人发言"
        + ("(MDT 讨论场景,必须做说话人分离)" if enable_diarization else "(单人录音,speaker_id 统一为 'S0')")
        + ";若无法分离,speaker_id 全部 'S0'。\n"
        "4) 不要总结、不要解读,只忠实转写。\n"
        f"5) 医学热词优先识别:{hotword_text}\n"
        "6) 严格输出 JSON 对象:{\"segments\": [...], \"duration\": <总时长秒>},"
        "禁止 markdown 代码块,禁止解释文字。"
    )

    user_text = (
        f"以下是一段「{ '患者诉求录音' if voice_type == 'patient_request' else 'MDT 讨论录音' }」,"
        "请按 system 中的规范转写并返回 JSON。"
    )

    # 火山方舟多模态音频 content block(已对照 §2377589 校对):
    #   - 外层 type 是 "audio"(不是 OpenAI 的 "input_audio",也不是 "audio_url")
    #   - base64 → audio.data;格式 → audio.format(mp3 / wav)
    audio_block: Dict[str, Any] = {
        "type": "audio",
        "audio": {
            "data": audio_b64,
            "format": audio_format,
        },
    }

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                audio_block,
                {"type": "text", "text": user_text},
            ],
        },
    ]


def _parse_response(raw: str, audio_bytes_len: int) -> Dict[str, Any]:
    """解析 LLM 返回的 JSON,降级为单 segment 处理异常。"""
    text = raw.strip()
    # 剥 markdown 围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "volc_audio_non_json_fallback",
            preview=text[:200],
        )
        # 兜底:把整段当一段返回,避免上层崩溃
        return {
            "segments": [
                {"speaker_id": "S0", "start": 0.0, "end": 0.0, "text": text[:5000]}
            ],
            "duration": 0.0,
            "num_speakers": 1,
        }

    segments_raw = payload.get("segments") or []
    segments: List[Dict[str, Any]] = []
    speakers: set[str] = set()
    for seg in segments_raw:
        if not isinstance(seg, dict):
            continue
        sp = str(seg.get("speaker_id") or seg.get("speaker") or "S0")
        speakers.add(sp)
        try:
            start = float(seg.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(seg.get("end") or 0.0)
        except (TypeError, ValueError):
            end = 0.0
        segments.append(
            {
                "speaker_id": sp,
                "start": start,
                "end": end,
                "text": str(seg.get("text") or "").strip(),
            }
        )

    try:
        duration = float(payload.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and segments:
        duration = max((s["end"] for s in segments), default=0.0)

    return {
        "segments": segments,
        "duration": duration,
        "num_speakers": max(len(speakers), 1),
    }


# ---- 对外接口:与 infer_client.asr_transcribe 接口对齐 ----


def transcribe(
    audio_bytes: bytes,
    *,
    filename: str,
    voice_type: str = "mdt_discussion",
    hotwords: Optional[List[str]] = None,
    enable_diarization: bool = True,
) -> Dict[str, Any]:
    """ASR 转写(火山豆包音频模型)。

    Args:
        audio_bytes: 原始音频字节(mp3/wav/m4a 等)
        filename: 文件名(用于推断格式)
        voice_type: patient_request | mdt_discussion
        hotwords: 医学热词列表(注入 prompt)
        enable_diarization: True=MDT 多人,需说话人分离

    Returns:
        {"segments": [{speaker_id, start, end, text}], "duration", "num_speakers"}

    Raises:
        VolcAudioError: API 调用失败或返回格式异常无法兜底。
    """
    if not audio_bytes:
        raise VolcAudioError("音频字节为空")

    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    fmt = _audio_format_token(filename)
    messages = _build_messages(
        audio_b64=audio_b64,
        audio_format=fmt,
        voice_type=voice_type,
        hotwords=hotwords or [],
        enable_diarization=enable_diarization,
    )

    logger.info(
        "volc_audio_call_start",
        model=settings.doubao_audio_model,
        bytes=len(audio_bytes),
        fmt=fmt,
        voice_type=voice_type,
        diarization=enable_diarization,
    )

    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.doubao_audio_model,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        logger.error(
            "volc_audio_call_failed",
            error=type(e).__name__,
            detail=str(e)[:300],
        )
        raise VolcAudioError(f"音频理解调用失败: {e}") from e

    result = _parse_response(raw, len(audio_bytes))
    logger.info(
        "volc_audio_call_ok",
        segments=len(result["segments"]),
        speakers=result["num_speakers"],
        duration=result["duration"],
    )
    return result


def is_available() -> Tuple[bool, str]:
    """配置自检 — 供 /api/v1/health 或启动 banner 使用。"""
    if not settings.doubao_api_key:
        return False, "DOUBAO_API_KEY 未配置"
    if not settings.doubao_audio_model:
        return False, "DOUBAO_AUDIO_MODEL 未配置"
    return True, "ok"
