"""音频转码单测 — 临床上场关键路径,任一断必拦发布。

不依赖网络/MinIO/数据库:只用 ffmpeg 自己合成一段静音音频,验证转码端到端能
1) 把 webm → mp3
2) 把 m4a → mp3
3) 把 wav → mp3
4) 错误输入抛 TranscodeError

环境要求:ffmpeg 在 PATH(CI runner 和 Dockerfile 都已安装)。本地缺 ffmpeg
则整个测试模块 skip,不算失败。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

ffmpeg_missing = shutil.which("ffmpeg") is None
pytestmark = pytest.mark.skipif(
    ffmpeg_missing, reason="本地无 ffmpeg — 跳过(Docker 镜像内一定有,CI 会跑)"
)


def _synth_silence(fmt: str, seconds: int = 2) -> bytes:
    """用 ffmpeg 合成一段静音音频返字节,模拟浏览器录到的真实容器。

    m4a (ipod muxer) 需要 seekable 输出,所以统一走临时文件,再读回字节。
    """
    if fmt == "webm":
        codec_args = ["-c:a", "libopus", "-b:a", "32k"]
        ext = "webm"
    elif fmt == "m4a":
        codec_args = ["-c:a", "aac", "-b:a", "32k"]
        ext = "m4a"
    elif fmt == "wav":
        codec_args = ["-c:a", "pcm_s16le"]
        ext = "wav"
    elif fmt == "ogg":
        codec_args = ["-c:a", "libopus", "-b:a", "32k"]
        ext = "ogg"
    else:
        raise ValueError(fmt)

    with tempfile.TemporaryDirectory(prefix="mdt-synth-") as tmp:
        out_path = os.path.join(tmp, f"sample.{ext}")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=mono:sample_rate=16000",
            "-t", str(seconds),
            *codec_args,
            out_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        with open(out_path, "rb") as f:
            return f.read()


def test_transcode_webm_to_mp3():
    from services.audio_transcode import transcode_to_mp3

    try:
        src = _synth_silence("webm", seconds=2)
    except subprocess.CalledProcessError:
        pytest.skip("ffmpeg 不支持 libopus,跳过(生产镜像 apt 装的 ffmpeg 默认含)")
    assert len(src) > 100

    mp3 = transcode_to_mp3(src, src_mime="audio/webm;codecs=opus")
    # mp3 容器以 "ID3" 标签或 0xFFFB sync word 开头
    assert mp3[:3] == b"ID3" or (mp3[0] == 0xFF and (mp3[1] & 0xE0) == 0xE0), (
        f"产物不像 mp3:{mp3[:8].hex()}"
    )
    assert len(mp3) > 100


def test_transcode_m4a_to_mp3():
    from services.audio_transcode import transcode_to_mp3

    src = _synth_silence("m4a", seconds=2)
    mp3 = transcode_to_mp3(src, src_mime="audio/mp4")
    assert mp3[:3] == b"ID3" or (mp3[0] == 0xFF and (mp3[1] & 0xE0) == 0xE0)


def test_transcode_wav_to_mp3():
    from services.audio_transcode import transcode_to_mp3

    src = _synth_silence("wav", seconds=2)
    mp3 = transcode_to_mp3(src, src_mime="audio/wav")
    assert mp3[:3] == b"ID3" or (mp3[0] == 0xFF and (mp3[1] & 0xE0) == 0xE0)


def test_transcode_empty_raises():
    from services.audio_transcode import TranscodeError, transcode_to_mp3

    with pytest.raises(TranscodeError):
        transcode_to_mp3(b"")


def test_transcode_garbage_raises():
    """完全无音频特征的随机字节应被 ffmpeg 拒,转码模块抛 TranscodeError 而非崩。"""
    from services.audio_transcode import TranscodeError, transcode_to_mp3

    junk = b"\x00\x01\x02not-an-audio-file" * 100
    with pytest.raises(TranscodeError):
        transcode_to_mp3(junk, src_mime="application/octet-stream")


def test_mime_to_ext_mapping():
    """关键 MIME 类型应映射到正确 ffmpeg 识别的扩展名。"""
    from services.audio_transcode import _ext_from_mime

    assert _ext_from_mime("audio/webm;codecs=opus") == "webm"
    assert _ext_from_mime("audio/mp4") == "m4a"
    assert _ext_from_mime("audio/x-m4a") == "m4a"
    assert _ext_from_mime("audio/wav") == "wav"
    assert _ext_from_mime("audio/x-wav") == "wav"
    assert _ext_from_mime("audio/mpeg") == "mp3"
    assert _ext_from_mime("audio/ogg;codecs=opus") == "ogg"
    assert _ext_from_mime("audio/unknown") == "bin"
    assert _ext_from_mime(None) == "bin"
    assert _ext_from_mime(None, fallback="webm") == "webm"


def test_ffmpeg_available_returns_truthy_in_env():
    from services.audio_transcode import ffmpeg_available

    ok, detail = ffmpeg_available()
    assert ok, f"ffmpeg 在 PATH 但探活失败: {detail}"
    assert "ffmpeg" in detail.lower() or detail == "ffmpeg ok"
