"""Command line: ``zhiyi paper.pdf -o out/ --service openai --api-key …``"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import __version__
from .config import Config


def _pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            low, high = part.split("-", 1)
            pages.update(range(int(low) - 1, int(high)))
        else:
            pages.add(int(part) - 1)
    return sorted(pages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zhiyi", description="知译 PDF 2：论文 PDF 翻译与排版",
                                     epilog="服务管理：zhiyi service start|stop|status|prepare [--port N]")
    parser.add_argument("pdf", nargs="?", help="要翻译的 PDF")
    parser.add_argument("-o", "--out", default=".", help="输出目录")
    parser.add_argument("--lang-in", default="en")
    parser.add_argument("--lang-out", default="zh-CN")
    parser.add_argument("--service", default=None, choices=["openai", "google", "pseudo", "file"])
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None, help="也可用环境变量 ZHIYI_API_KEY")
    parser.add_argument("--model", default=None)
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--qps", type=float, default=None)
    parser.add_argument("--pages", default=None, help="如 1-3,7")
    parser.add_argument("--glossary", default=None, help="术语表 CSV：原文,译文")
    parser.add_argument("--no-auto-glossary", action="store_true")
    parser.add_argument("--translate-references", action="store_true")
    parser.add_argument("--no-tables", action="store_true")
    parser.add_argument("--font-scale", type=float, default=None)
    parser.add_argument("--no-mono", action="store_true")
    parser.add_argument("--no-dual", action="store_true")
    parser.add_argument("--dual-mode", default=None, choices=["LR", "TB"])
    parser.add_argument("--translations", default=None, help="离线译文 JSON（service=file）")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--config", default=None, help="JSON 配置文件，命令行参数优先")
    parser.add_argument("--serve", action="store_true", help="启动供 Zotero 插件调用的本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8893)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"zhiyi {__version__}")
    return parser


def config_from_args(args) -> Config:
    data: dict = {}
    if args.config:
        with open(args.config, encoding="utf-8") as handle:
            data.update(json.load(handle))
    overrides = {
        "lang_in": args.lang_in, "lang_out": args.lang_out, "service": args.service, "base_url": args.base_url,
        "api_key": args.api_key or os.environ.get("ZHIYI_API_KEY"), "model": args.model, "threads": args.threads,
        "qps": args.qps, "pages": _pages(args.pages), "glossary_path": args.glossary, "font_scale": args.font_scale,
        "dual_mode": args.dual_mode, "translations_file": args.translations,
    }
    data.update({k: v for k, v in overrides.items() if v is not None})
    if args.no_auto_glossary:
        data["auto_glossary"] = False
    if args.translate_references:
        data["translate_references"] = True
    if args.no_tables:
        data["translate_tables"] = False
    if args.no_mono:
        data["mono"] = False
    if args.no_dual:
        data["dual"] = False
    if args.no_cache:
        data["cache"] = False
    if args.translations and "service" not in data:
        data["service"] = "file"
    return Config.from_dict(data)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["service"]:
        from .service import main as service_main

        return service_main(argv[1:])
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.serve:
        from .server import serve

        serve(args.host, args.port)
        return 0
    if not args.pdf:
        build_parser().print_usage()
        return 2

    from .pipeline import translate_pdf

    last = [""]

    interactive = sys.stderr.isatty()

    def progress(stage: str, done: int, total: int) -> None:
        label = {"analyze": "分析版面", "translate": "翻译", "typeset": "排版", "render": "写入"}.get(stage, stage)
        if interactive:
            print(f"\r{label} {done}/{total}      ", end="", file=sys.stderr, flush=True)
        elif stage != last[0]:
            last[0] = stage
            print(f"{label}…", file=sys.stderr, flush=True)

    try:
        report = translate_pdf(args.pdf, args.out, config_from_args(args), progress)
    except Exception as error:
        print(f"\n失败：{error}", file=sys.stderr)
        if args.verbose:
            raise
        return 1
    if interactive:
        print(file=sys.stderr)
    layout = report["layout"]
    print(f"完成，用时 {report['seconds']['total']}s；段落 {report['analysis']['units']}（跨栏/跨页/跨公式合并 "
          f"{report['analysis']['chained']}），单独缩小字号 {layout['shrunk'] + layout['shrunk_hard']}，放不下而保留原文 {layout['kept_original']}")
    for kind, path in report["outputs"].items():
        print(f"  {kind}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
