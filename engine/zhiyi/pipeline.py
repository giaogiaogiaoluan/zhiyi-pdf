"""End-to-end translation of one PDF."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pymupdf

from . import __version__, render, typeset
from .analyze import analyze
from .config import Config
from .translate import make_backend, translate_units

log = logging.getLogger(__name__)


def output_paths(source: Path, out_dir: Path, config: Config) -> dict[str, Path]:
    stem = source.stem
    return {
        "mono": out_dir / f"{stem}.{config.lang_out}.mono.pdf",
        "dual": out_dir / f"{stem}.{config.lang_out}.dual.pdf",
        "report": out_dir / f"{stem}.{config.lang_out}.report.json",
    }


def translate_pdf(source: str | Path, out_dir: str | Path, config: Config, progress=None) -> dict:
    """Translate ``source`` and write the requested outputs. Returns a report."""
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    timings: dict[str, float] = {}

    doc = pymupdf.open(str(source))
    if doc.needs_pass:
        raise RuntimeError("PDF 已加密，无法处理")
    if not doc.is_pdf:
        raise RuntimeError("仅支持 PDF 文件")

    t = time.time()
    analysis = analyze(doc, config.pages, translate_references=config.translate_references, progress=progress)
    doc.close()
    units = analysis.units
    if not config.translate_tables:
        units = [u for u in units if u.role != "table"]
    if not config.translate_captions:
        units = [u for u in units if u.role != "caption"]
    timings["analyze"] = time.time() - t
    if not units:
        raise RuntimeError("没有找到可翻译的文字（可能是扫描版 PDF，需要先做 OCR）")

    t = time.time()
    backend = make_backend(config)
    try:
        translation_stats = translate_units(units, backend, config, progress)
    finally:
        backend.close()
    timings["translate"] = time.time() - t
    translated = [u for u in units if u.translation]
    if not translated:
        raise RuntimeError("翻译全部失败，请检查翻译服务设置和网络")

    t = time.time()
    placements, layout_report = typeset.typeset(translated, config, progress)
    timings["typeset"] = time.time() - t

    t = time.time()
    paths = output_paths(source, out_dir, config)
    result = render.render(str(source), analysis.pages, translated, placements, progress)
    outputs: dict[str, str] = {}
    layout_report["stuck_glyphs"] = getattr(result, "_zhiyi_stuck", 0)
    try:
        result.subset_fonts()
    except Exception as error:
        log.warning("font subsetting skipped: %s", error)
    if config.mono:
        result.save(str(paths["mono"]), garbage=4, deflate=True, use_objstms=1)
        outputs["mono"] = str(paths["mono"])
    if config.dual:
        render.make_dual(str(source), result, str(paths["dual"]), config.dual_mode)
        outputs["dual"] = str(paths["dual"])
    result.close()
    timings["render"] = time.time() - t

    notes = [{"unit": u.id, "page": u.page + 1, "notes": u.notes} for u in units if u.notes]
    report = {
        "engine": __version__,
        "source": source.name,
        "outputs": outputs,
        "analysis": analysis.stats,
        "body_size": analysis.body_size,
        "translation": translation_stats,
        "layout": layout_report,
        "notes": notes,
        "seconds": {k: round(v, 1) for k, v in timings.items()} | {"total": round(time.time() - started, 1)},
    }
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs["report"] = str(paths["report"])
    return report
