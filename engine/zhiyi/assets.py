"""Locate (and, when missing, download) the layout model and fonts.

Search order: ``$ZHIYI_HOME/assets`` → an existing BabelDOC cache (so machines
that already ran pdf2zh need no download) → upstream mirrors.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

MODEL_NAME = "doclayout_yolo_docstructbench_imgsz1024.onnx"

_MODEL_URLS = [
    "https://www.modelscope.cn/models/AI-ModelScope/DocLayout-YOLO-DocStructBench-onnx/resolve/master/" + MODEL_NAME,
    "https://hf-mirror.com/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/" + MODEL_NAME,
    "https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/" + MODEL_NAME,
]
_FONT_URLS = [
    "https://www.modelscope.cn/datasets/awwaawwa/BabelDOCAssets/resolve/master/fonts/{name}",
    "https://hf-mirror.com/datasets/awwaawwa/BabelDOC-Assets/resolve/main/fonts/{name}",
    "https://huggingface.co/datasets/awwaawwa/BabelDOC-Assets/resolve/main/fonts/{name}",
    "https://raw.githubusercontent.com/funstory-ai/BabelDOC-Assets/refs/heads/main/fonts/{name}",
]
_MIN_BYTES = {"models": 10_000_000, "fonts": 100_000}


def home() -> Path:
    return Path(os.environ.get("ZHIYI_HOME", "~/.zhiyi")).expanduser()


def _candidates(kind: str, name: str) -> list[Path]:
    return [
        home() / "assets" / kind / name,
        Path("~/.cache/babeldoc").expanduser() / kind / name,
    ]


def _download(urls: list[str], target: Path, min_bytes: int) -> bool:
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        tmp = None
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    tmp = Path(handle.name)
                    for chunk in response.iter_bytes(1 << 16):
                        handle.write(chunk)
            if tmp.stat().st_size < min_bytes:
                raise OSError(f"truncated download ({tmp.stat().st_size} bytes)")
            shutil.move(str(tmp), target)
            log.info("downloaded %s", target.name)
            return True
        except Exception as error:  # try the next mirror
            log.warning("download failed from %s: %s", url.split("/")[2], error)
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    return False


def find(kind: str, name: str, *, download: bool = True) -> Path | None:
    """Return a local path for an asset, downloading it on first use."""
    for path in _candidates(kind, name):
        if path.is_file() and path.stat().st_size >= _MIN_BYTES[kind]:
            return path
    if not download or os.environ.get("ZHIYI_OFFLINE") == "1":
        return None
    urls = _MODEL_URLS if kind == "models" else [u.format(name=name) for u in _FONT_URLS]
    target = _candidates(kind, name)[0]
    return target if _download(urls, target, _MIN_BYTES[kind]) else None


def model_path(*, download: bool = True) -> Path | None:
    return find("models", MODEL_NAME, download=download)


def font_path(name: str, *, download: bool = True) -> Path | None:
    return find("fonts", name, download=download)
