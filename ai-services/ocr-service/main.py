"""自部署 OCR 服务 - PaddleOCR PP-StructureV3
- POST /ocr/image: 单张图片 → {raw_text, blocks[], tables[], confidence}
- POST /ocr/pdf: 多页 PDF → 多页拼接
"""
from __future__ import annotations

import io
import logging
import os
from typing import Any, Dict, List

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image

try:
    # PP-StructureV3 入口
    from paddleocr import PPStructureV3
except ImportError:  # pragma: no cover
    PPStructureV3 = None  # type: ignore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ocr")

app = FastAPI(title="TumorBoard OCR Service", version="0.1.0")

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        if PPStructureV3 is None:
            raise RuntimeError("paddleocr 未安装,无法启动 OCR")
        log.info("loading_paddleocr_engine")
        _engine = PPStructureV3(
            lang="ch",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
        )
    return _engine


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "engine_loaded": _engine is not None,
        "device": os.environ.get("CUDA_VISIBLE_DEVICES", "cpu"),
    }


def _flatten(result: Any) -> Dict[str, Any]:
    """把 PP-Structure 的层次化输出压成 {raw_text, blocks[], tables[], confidence}。"""
    raw_lines: List[str] = []
    blocks: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    confidences: List[float] = []

    # PP-Structure 返回 list[dict],每项 type=text/table/title/figure
    if not isinstance(result, list):
        result = [result]

    for item in result:
        if not isinstance(item, dict):
            continue
        itype = item.get("type") or item.get("category") or ""
        text = item.get("text") or item.get("res", {}).get("text") if isinstance(item.get("res"), dict) else item.get("text")
        if itype in {"text", "title", "list", "paragraph"} and text:
            raw_lines.append(text)
            blocks.append({
                "text": text,
                "bbox": item.get("bbox") or item.get("region"),
                "confidence": float(item.get("confidence") or item.get("score") or 0.0),
            })
            if isinstance(item.get("confidence"), (int, float)):
                confidences.append(float(item["confidence"]))
        elif itype == "table":
            cells: List[Dict[str, Any]] = []
            res = item.get("res") or {}
            html = res.get("html") if isinstance(res, dict) else None
            rows = res.get("cells") if isinstance(res, dict) else []
            if isinstance(rows, list):
                for r_idx, row in enumerate(rows):
                    if not isinstance(row, list):
                        continue
                    for c_idx, cell in enumerate(row):
                        if isinstance(cell, dict):
                            cells.append({
                                "row": r_idx,
                                "col": c_idx,
                                "text": cell.get("text", ""),
                            })
                        else:
                            cells.append({"row": r_idx, "col": c_idx, "text": str(cell)})
            tables.append({
                "cells": cells,
                "row_count": len(rows) if isinstance(rows, list) else 0,
                "col_count": max((len(r) for r in rows if isinstance(r, list)), default=0),
                "html": html,
            })
            if html:
                raw_lines.append(html)

    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "raw_text": "\n".join(raw_lines),
        "blocks": blocks,
        "tables": tables,
        "confidence": confidence,
    }


@app.post("/ocr/image")
async def ocr_image(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        engine = get_engine()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"engine_unavailable: {e}") from e
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    img = Image.open(io.BytesIO(data)).convert("RGB")
    import numpy as np
    arr = np.asarray(img)
    result = engine.predict(arr)
    flat = _flatten(result)
    log.info(
        "ocr_image_done",
        extra={
            "blocks": len(flat["blocks"]),
            "tables": len(flat["tables"]),
            "confidence": flat["confidence"],
        },
    )
    return flat


@app.post("/ocr/pdf")
async def ocr_pdf(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        engine = get_engine()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"engine_unavailable: {e}") from e
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        from pdf2image import convert_from_bytes
    except ImportError as e:
        raise HTTPException(status_code=500, detail="pdf2image not installed") from e
    pages = convert_from_bytes(data, dpi=200)
    merged: Dict[str, Any] = {"raw_text": "", "blocks": [], "tables": [], "confidence": 0.0}
    confs: List[float] = []
    for i, page in enumerate(pages):
        import numpy as np
        arr = np.asarray(page.convert("RGB"))
        result = engine.predict(arr)
        flat = _flatten(result)
        merged["raw_text"] += f"\n\n[第 {i+1} 页]\n" + flat["raw_text"]
        merged["blocks"].extend(flat["blocks"])
        merged["tables"].extend(flat["tables"])
        if flat["confidence"]:
            confs.append(flat["confidence"])
    merged["confidence"] = sum(confs) / len(confs) if confs else 0.0
    return merged
