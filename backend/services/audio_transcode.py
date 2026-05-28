"""音频转码 - 把浏览器/手机产出的任意格式统一转 mp3 16kHz mono。

为什么必须有
-----------
火山引擎豆包音频理解 API 只接受 `mp3 / wav` 两种 format,但:
- Android Chrome `MediaRecorder` 默认输出 `audio/webm;codecs=opus`
- iOS Safari `MediaRecorder` (16+) 默认输出 `audio/mp4` (m4a/aac)
- 微信内置浏览器 (X5 内核) 行为更杂,可能给 webm/m4a/3gp

如果直接把原字节当 mp3 喂给豆包 API,要么 400,要么乱解,要么静默返回空 — 临床上场即崩盘。
所以 finalize 阶段必须用 ffmpeg 转码。

红线
----
- 转码在本服务器进程内执行,不外传(原音频后续才走火山自有云,policy v1.1 已声明)。
- 输出固定 16kHz mono mp3 / 64kbps — 火山豆包接收稳定,30 分钟会议约 14 MB。
- ffmpeg 不在则抛 RuntimeError,启动 banner 探活会拦下。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger("audio_transcode")


class TranscodeError(RuntimeError):
    """音频转码异常 — 调用方应 500 返客户端,提示稍后重试。"""


# 输出参数:豆包音频理解推荐 16kHz 单声道,mp3 64kbps 体积小且转写质量足够
_TARGET_SR = 16000
_TARGET_CH = 1
_TARGET_BITRATE = "64k"


def ffmpeg_available() -> Tuple[bool, str]:
    """启动期/健康检查用 — 探 ffmpeg 是否可执行。"""
    path = shutil.which("ffmpeg")
    if not path:
        return False, "ffmpeg 不在 PATH"
    try:
        out = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        first_line = (out.stdout or "").splitlines()[0] if out.stdout else ""
        return out.returncode == 0, first_line or "ffmpeg ok"
    except Exception as e:  # noqa: BLE001
        return False, f"ffmpeg 探活失败: {e}"


def _ext_from_mime(mime: Optional[str], fallback: str = "bin") -> str:
    """从 MIME 推断输入扩展名(供 ffmpeg 识别容器格式)。"""
    if not mime:
        return fallback
    m = mime.lower()
    # 只看主类型 + 关键子类型,忽略 codec 参数
    if "webm" in m:
        return "webm"
    if "mp4" in m or "m4a" in m or "aac" in m:
        return "m4a"
    if "wav" in m or "x-wav" in m or "wave" in m:
        return "wav"
    if "mp3" in m or "mpeg" in m:
        return "mp3"
    if "ogg" in m or "opus" in m:
        return "ogg"
    if "3gp" in m or "amr" in m:
        return "3gp"
    return fallback


def transcode_to_mp3(
    src_bytes: bytes,
    *,
    src_mime: Optional[str] = None,
    src_ext: Optional[str] = None,
    timeout_sec: int = 180,
) -> bytes:
    """把任意输入字节转成 16kHz mono mp3。

    Args:
        src_bytes: 输入音频原字节(浏览器拼接好的整段)
        src_mime: 浏览器声明的 MIME(MediaRecorder.mimeType),首选依据
        src_ext: 文件后缀兜底(如 "webm"),mime 取不到时用
        timeout_sec: ffmpeg 超时(30 分钟音频转码常态 30-90s,留 3 分钟)

    Returns:
        mp3 字节(可直接 b64 喂给火山豆包音频理解 API)

    Raises:
        TranscodeError: ffmpeg 缺失 / 转码失败 / 超时 / 产物为空
    """
    if not src_bytes:
        raise TranscodeError("源音频为空,无法转码")

    ok, detail = ffmpeg_available()
    if not ok:
        raise TranscodeError(f"ffmpeg 不可用: {detail}")

    in_ext = _ext_from_mime(src_mime, fallback=src_ext or "bin")
    in_size = len(src_bytes)

    # 用临时文件而不是 pipe — 部分容器(m4a/mp4)需要 seekable 输入
    with tempfile.TemporaryDirectory(prefix="mdt-trans-") as tmp:
        in_path = os.path.join(tmp, f"in.{in_ext}")
        out_path = os.path.join(tmp, "out.mp3")
        with open(in_path, "wb") as f:
            f.write(src_bytes)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", in_path,
            "-vn",                   # 丢弃可能的视频轨
            "-ac", str(_TARGET_CH),  # 单声道
            "-ar", str(_TARGET_SR),  # 16kHz
            "-b:a", _TARGET_BITRATE, # 64kbps
            "-f", "mp3",
            out_path,
        ]
        logger.info(
            "audio_transcode_start",
            in_ext=in_ext,
            in_bytes=in_size,
            sr=_TARGET_SR,
            ch=_TARGET_CH,
        )
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as e:
            raise TranscodeError(
                f"ffmpeg 转码超时({timeout_sec}s),源 {in_size/1024/1024:.1f}MB"
            ) from e

        if res.returncode != 0:
            err_tail = (res.stderr or "").strip().splitlines()[-5:]
            raise TranscodeError(
                f"ffmpeg 转码失败 (rc={res.returncode}): {' | '.join(err_tail)}"
            )

        if not os.path.exists(out_path):
            raise TranscodeError("ffmpeg 退出 0 但未产出 mp3,源格式可能损坏")

        with open(out_path, "rb") as f:
            mp3_bytes = f.read()

        if len(mp3_bytes) < 100:
            raise TranscodeError(
                f"转码产物过小 ({len(mp3_bytes)} 字节),源可能不含音频轨"
            )

        logger.info(
            "audio_transcode_done",
            in_ext=in_ext,
            in_bytes=in_size,
            out_bytes=len(mp3_bytes),
            ratio=f"{len(mp3_bytes)/in_size:.2f}",
        )
        return mp3_bytes
