"""Layout regions from the DocLayout-YOLO (DocStructBench) ONNX model.

The model only *labels* areas (title, plain text, figure, table, formula,
caption, abandon). Paragraph boundaries are decided geometrically elsewhere,
so a slightly-off box cannot split or glue paragraphs on its own.
"""

from __future__ import annotations

import ast
import logging
import threading

import numpy as np
import pymupdf

from . import assets
from .model import Region

log = logging.getLogger(__name__)

IMG_SIZE = 1024
SCORE_MIN = 0.25

_session = None
_names: dict[int, str] = {}
_lock = threading.Lock()


def _load():
    global _session, _names
    with _lock:
        if _session is not None:
            return _session
        import onnxruntime

        path = assets.model_path()
        if path is None:
            raise RuntimeError("版面模型不可用：下载失败，且本机没有缓存")
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        session = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        meta = session.get_modelmeta().custom_metadata_map
        _names = ast.literal_eval(meta["names"])
        _session = session
        return session


def _letterbox(page: pymupdf.Page):
    """Render the page so its long side is IMG_SIZE and pad to a square."""
    rect = page.rect
    scale = IMG_SIZE / max(rect.width, rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]
    canvas = np.full((IMG_SIZE, IMG_SIZE, 3), 114, np.uint8)
    height, width = min(pix.height, IMG_SIZE), min(pix.width, IMG_SIZE)
    top, left = (IMG_SIZE - height) // 2, (IMG_SIZE - width) // 2
    canvas[top:top + height, left:left + width] = image[:height, :width]
    return canvas, scale, left, top


def _iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def detect(page: pymupdf.Page) -> list[Region]:
    session = _load()
    canvas, scale, left, top = _letterbox(page)
    tensor = (canvas.transpose(2, 0, 1)[None].astype(np.float32)) / 255.0
    with _lock:
        rows = session.run(None, {"images": tensor})[0][0]
    regions: list[Region] = []
    for x0, y0, x1, y1, score, cls in rows[rows[:, 4] > SCORE_MIN]:
        box = ((x0 - left) / scale, (y0 - top) / scale, (x1 - left) / scale, (y1 - top) / scale)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            continue
        regions.append(Region(_names.get(int(cls), "plain text"), box, float(score)))
    # The model is NMS-free but still emits near-duplicates now and then.
    regions.sort(key=lambda r: -r.score)
    kept: list[Region] = []
    for region in regions:
        if all(_iou(region.box, other.box) < 0.8 for other in kept):
            kept.append(region)
    return kept
