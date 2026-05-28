"""自部署 ASR 服务 - FunASR Paraformer-large + cam++ 说话人分离 + ct-punc 标点

POST /asr/transcribe:
- file: 音频文件(mp3/wav/m4a)
- enable_diarization: 是否做说话人分离
- hotwords: 逗号分隔热词

返回 {segments: [{speaker, start, end, text}], num_speakers, duration, text}
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

try:
    from funasr import AutoModel
except ImportError:  # pragma: no cover
    AutoModel = None  # type: ignore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asr")

app = FastAPI(title="TumorBoard ASR Service", version="0.1.0")

_model = None
_hotwords_default: List[str] = []


def _load_hotwords_file() -> List[str]:
    p = Path(__file__).parent / "hotwords.txt"
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def get_model():
    global _model, _hotwords_default
    if _model is None:
        if AutoModel is None:
            raise RuntimeError("funasr 未安装,无法启动 ASR")
        log.info("loading_funasr_model")
        # paraformer-large + 说话人分离 cam++ + 标点 ct-punc + 语音活动 fsmn-vad
        _model = AutoModel(
            model="iic/speech_paraformer-large-vad-punc-spk_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            spk_model="iic/speech_campplus_sv_zh-cn_16k-common",
            vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            punc_model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
            device="cuda" if os.environ.get("CUDA_VISIBLE_DEVICES") else "cpu",
        )
        _hotwords_default = _load_hotwords_file()
        log.info("funasr_loaded", extra={"hotwords_count": len(_hotwords_default)})
    return _model


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "model_loaded": _model is not None,
        "device": os.environ.get("CUDA_VISIBLE_DEVICES", "cpu"),
    }


def _parse_segments(funasr_out: Any) -> List[Dict[str, Any]]:
    """FunASR 输出结构因版本而异,这里做尽量兼容的解析。
    期望产出 [{"speaker", "start", "end", "text"}]。
    """
    segments: List[Dict[str, Any]] = []
    if not isinstance(funasr_out, list):
        funasr_out = [funasr_out]
    for piece in funasr_out:
        if not isinstance(piece, dict):
            continue
        # 优先 sentence_info / sentences
        sentences = piece.get("sentence_info") or piece.get("sentences") or []
        if sentences:
            for s in sentences:
                segments.append({
                    "speaker": str(s.get("spk") or s.get("speaker") or "SP00"),
                    "start": float(s.get("start", 0)) / 1000.0,
                    "end": float(s.get("end", 0)) / 1000.0,
                    "text": str(s.get("text", "")).strip(),
                })
        else:
            text = piece.get("text", "") or ""
            if text:
                segments.append({
                    "speaker": "SP00",
                    "start": 0.0,
                    "end": float(piece.get("duration", 0.0)),
                    "text": text,
                })
    # 规范化 speaker id: SP00 → SP01...
    spk_map: Dict[str, str] = {}
    for seg in segments:
        sp = seg["speaker"]
        if sp not in spk_map:
            spk_map[sp] = f"SP{len(spk_map)+1:02d}"
        seg["speaker"] = spk_map[sp]
    return segments


@app.post("/asr/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    enable_diarization: str = Form("true"),
    hotwords: Optional[str] = Form(None),
) -> Dict[str, Any]:
    try:
        model = get_model()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"model_unavailable: {e}") from e

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")

    # FunASR 接受文件路径
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "audio")[1] or ".mp3", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        custom_hotwords = _hotwords_default[:]
        if hotwords:
            custom_hotwords.extend([h.strip() for h in hotwords.split(",") if h.strip()])
        hotword_str = " ".join(custom_hotwords[:500]) if custom_hotwords else None

        kwargs: Dict[str, Any] = {
            "input": tmp_path,
            "batch_size_s": 300,
            "merge_vad": True,
            "merge_length_s": 15,
        }
        if hotword_str:
            kwargs["hotword"] = hotword_str
        if enable_diarization.lower() not in {"true", "1", "yes"}:
            kwargs["sentence_timestamp"] = True
        log.info("asr_call_start", extra={"hotwords": len(custom_hotwords)})

        result = model.generate(**kwargs)
        segments = _parse_segments(result)
        full_text = "".join(s["text"] for s in segments)
        duration = max((s["end"] for s in segments), default=0.0)
        num_speakers = len({s["speaker"] for s in segments}) if segments else 0
        log.info(
            "asr_done",
            extra={"segments": len(segments), "speakers": num_speakers, "duration": duration},
        )
        return {
            "text": full_text,
            "segments": segments,
            "num_speakers": num_speakers,
            "duration": duration,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
