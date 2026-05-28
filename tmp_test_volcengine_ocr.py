"""烟雾测试:火山 OCR 路径(mocked VisualService,不走真实 API)。

目的:在 AK/SK + DOUBAO key 都还没填好的情况下,验证代码逻辑:
  1. _make_visual_service 在缺凭证时抛 VolcOcrError
  2. ocr_image 正常解析 ocr_normal 返回格式
  3. ocr_pages 多页拼接 + 单页失败降级
  4. _prepare_images_for_vision 能把 PDF 拆成 PNG 列表
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

# 给 config 一个最小可用环境
os.environ.setdefault("LLM_FALLBACK_PROVIDERS", "")
os.environ.setdefault("DOUBAO_API_KEY", "placeholder")


# ---------- 1. 缺 AK/SK 时应抛错 ----------
print("[1/5] 缺 AK/SK 时,_make_visual_service 应抛 VolcOcrError ...")
from importlib import reload
import config
from services import volcengine_ocr

# 直接 monkey-patch settings(不依赖 env,因为 pydantic-settings 会读 .env 文件)
_real_ak = config.settings.volcengine_ak
_real_sk = config.settings.volcengine_sk
config.settings.volcengine_ak = None
config.settings.volcengine_sk = None

try:
    volcengine_ocr._make_visual_service()
    print("  ❌ 应该抛错但没抛")
    sys.exit(1)
except volcengine_ocr.VolcOcrError as e:
    print(f"  ✅ 正确抛出: {e}")


# ---------- 2. 注入 mock AK/SK,mock SDK,验证 ocr_image ----------
print("\n[2/5] 注入 mock AK/SK 和 SDK,验证 ocr_image 解析 ocr_normal 响应 ...")
config.settings.volcengine_ak = "test-ak"
config.settings.volcengine_sk = "test-sk"
reload(volcengine_ocr)

fake_visual = MagicMock()
fake_visual.ocr_normal.return_value = {
    "code": 10000,
    "message": "Success",
    "data": {
        "words": [
            "XX 医院 检验报告单",
            "患者:[已遮挡]   病案号:P-00123",
            "CEA  12.8  ng/mL  ↑",
            "CA19-9  45.2  U/mL  ↑",
            "HGB  98  g/L  ↓",
        ],
        "word_boxes": [],
    },
}

with patch.object(volcengine_ocr, "_make_visual_service", return_value=fake_visual):
    r = volcengine_ocr.ocr_image(b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES")
    print(f"  raw_text 长度: {len(r['raw_text'])}")
    print(f"  words 数量:   {len(r['words'])}")
    print(f"  raw_text 前 60 字符: {r['raw_text'][:60]!r}")
    assert "CEA" in r["raw_text"] and "12.8" in r["raw_text"]
    assert r["confidence"] == 0.9
    print("  ✅ 响应解析正确")


# ---------- 3. ocr_pages 多页 + 单页失败降级 ----------
print("\n[3/5] ocr_pages 多页 + 某页失败时降级 ...")
call_count = {"n": 0}


def flaky_general_basic(body):
    call_count["n"] += 1
    if call_count["n"] == 2:
        # 第 2 页模拟服务端 4xx
        return {"code": 50001, "message": "ImageDecodeError", "data": {}}
    return {
        "code": 10000,
        "message": "Success",
        "data": {"words": [f"page-{call_count['n']} content"], "word_boxes": []},
    }


fake_visual2 = MagicMock()
fake_visual2.ocr_normal.side_effect = flaky_general_basic

with patch.object(volcengine_ocr, "_make_visual_service", return_value=fake_visual2):
    pages_in = [
        (b"page1-bytes", "image/png"),
        (b"page2-bytes", "image/png"),
        (b"page3-bytes", "image/png"),
    ]
    r = volcengine_ocr.ocr_pages(pages_in)
    print(f"  n_pages: {r['n_pages']}")
    print(f"  raw_text:\n{'-' * 40}\n{r['raw_text']}\n{'-' * 40}")
    print(f"  详情:")
    for d in r["pages"]:
        print(f"    {d}")
    assert r["n_pages"] == 3
    assert "page-1 content" in r["raw_text"]
    assert "[第 2 页 OCR 失败" in r["raw_text"]  # 降级标记
    assert "page-3 content" in r["raw_text"]  # 第 3 页(其实 mock 里 call 3 = page-3)
    print("  ✅ 单页失败不影响其他页")


# ---------- 4. _prepare_images_for_vision: PDF 拆页 ----------
print("\n[4/5] _prepare_images_for_vision: PDF→PNG 拆页 ...")
from PIL import Image, ImageDraw

img = Image.new("RGB", (300, 200), "white")
ImageDraw.Draw(img).text((20, 80), "Page 1 - test PDF", fill="black")
buf = io.BytesIO()
img.save(buf, "JPEG")
jpg_bytes = buf.getvalue()

import fitz

img_doc = fitz.open(stream=jpg_bytes, filetype="jpeg")
pdf_bytes = img_doc.convert_to_pdf()
img_doc.close()

# 不 reload celery_tasks(它依赖 celery),直接复制等价函数
from typing import List, Tuple

_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
}
_MAX_PAGES = 10


def _prepare(file_bytes, file_key, file_type) -> List[Tuple[bytes, str]]:
    ext = file_key.lower().rsplit(".", 1)[-1] if "." in file_key else ""
    if (file_type or "").lower() == "pdf" or ext == "pdf":
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            return [
                (page.get_pixmap(dpi=200).tobytes("png"), "image/png")
                for i, page in enumerate(doc)
                if i < _MAX_PAGES
            ]
        finally:
            doc.close()
    return [(file_bytes, _MIME_BY_EXT.get(ext, "image/jpeg"))]


pages = _prepare(pdf_bytes, "test.pdf", "pdf")
print(f"  拆出 {len(pages)} 页,mime={pages[0][1]}, 大小={len(pages[0][0]):,} bytes")
assert pages[0][0][:8] == b"\x89PNG\r\n\x1a\n"
print("  ✅ PDF→PNG 转换正常")

pages = _prepare(jpg_bytes, "test.jpg", None)
print(f"  单张 JPEG: mime={pages[0][1]}, 大小={len(pages[0][0]):,} bytes")
assert pages[0][1] == "image/jpeg"
print("  ✅ 图片透传正常")


# ---------- 5. healthcheck ----------
print("\n[5/5] healthcheck ...")
config.settings.volcengine_ak = "x"
config.settings.volcengine_sk = "y"
print(f"  AK/SK 都有 + SDK 已装 = {volcengine_ocr.healthcheck()}")
assert volcengine_ocr.healthcheck() is True  # SDK 已装 + 有 key

config.settings.volcengine_ak = None
print(f"  缺 AK = {volcengine_ocr.healthcheck()}")
assert volcengine_ocr.healthcheck() is False

# 恢复真实 key,避免后续测试串扰
config.settings.volcengine_ak = _real_ak
config.settings.volcengine_sk = _real_sk

print("\n✅ 所有 mock 路径测试通过")
print("\n下一步:在 .env 里填好 VOLCENGINE_AK/SK,跑真实图片测试。")
