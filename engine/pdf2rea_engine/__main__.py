"""``python -m pdf2rea_engine`` -- install / verify / remove the patch layer.

Used by ``scripts/install_engine.sh`` and by the Zotero plugin's "引擎补丁" panel.
The plugin writes the same JSON options file, so both paths stay equivalent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

PTH_NAME = "pdf2rea.pth"
PTH_CONTENT = "import pdf2rea_engine.bootstrap\n"
PKG_NAME = "pdf2rea_engine"


def discover_server(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    return os.path.join(os.path.expanduser("~"), ".pdf2rea", "server")


def require_private_server(server: str) -> None:
    marker = os.path.join(server, "pdf2rea-runtime.json")
    try:
        with open(marker, encoding="utf-8") as handle: owner = json.load(handle).get("owner")
    except (OSError, ValueError): owner = None
    if owner != "pdf2rea@qoder.dev" or os.path.islink(server):
        raise SystemExit("拒绝修改非 PDF2REA 独立环境；请先运行 scripts/setup_runtime.py")
    venv = os.path.join(server, "zotero-pdf2zh-next-venv")
    if os.path.islink(venv): raise SystemExit("独立 venv 不能是原版环境的符号链接")


def venv_paths(server: str) -> tuple[str, str]:
    """Return (venv python, site-packages) for the server's next engine venv."""
    home = os.path.expanduser("~")
    candidates = []
    for name in ("zotero-pdf2zh-next-venv", "pdf2zh-next-venv", ".venv", "venv"):
        candidates.append(os.path.join(server, name))
    venv = next((c for c in candidates if os.path.isdir(c)), None)
    if not venv:
        raise SystemExit(f"未找到引擎 venv：{server} 下没有 zotero-pdf2zh-next-venv")
    python = os.path.join(venv, "bin", "python")
    if not os.path.isfile(python):
        python = os.path.join(venv, "bin", "python3")
    site = subprocess.run(
        [python, "-c", "import site,sys;print(site.getsitepackages()[0])"],
        capture_output=True, text=True,
    )
    if site.returncode != 0:
        raise SystemExit(f"无法解析 site-packages：{site.stderr.strip()}")
    return python, site.stdout.strip()


def cmd_install(args) -> int:
    server = discover_server(args.server)
    require_private_server(server)
    python, site = venv_paths(server)
    source_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_pkg = os.path.join(source_root, PKG_NAME)
    if not os.path.isdir(source_pkg):
        raise SystemExit(f"找不到引擎源码目录：{source_pkg}")

    if args.dry_run:
        print(f"[dry-run] would copy {source_pkg} -> {site}")
        print(f"[dry-run] would write {os.path.join(site, PTH_NAME)}")
        return 0

    os.makedirs(site, exist_ok=True)
    target_pkg = os.path.join(site, PKG_NAME)
    if os.path.isdir(target_pkg):
        if args.force:
            shutil.rmtree(target_pkg)
        else:
            raise SystemExit(f"{target_pkg} 已存在，使用 --force 覆盖")
    shutil.copytree(source_pkg, target_pkg,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    with open(os.path.join(site, PTH_NAME), "w", encoding="utf-8") as handle:
        handle.write(PTH_CONTENT)

    options_path = os.path.join(server, "config", "pdf2rea.json")
    if not os.path.isfile(options_path) or args.force:
        os.makedirs(os.path.dirname(options_path), exist_ok=True)
        from pdf2rea_engine import options as options_module

        with open(options_path, "w", encoding="utf-8") as handle:
            json.dump(options_module.DEFAULTS, handle, ensure_ascii=False, indent=2)
        print(f"已写入默认增强配置：{options_path}")

    result = subprocess.run(
        [python, "-c", "import pdf2rea_engine, json; "
                       "print(json.dumps(pdf2rea_engine.apply(), ensure_ascii=False))"],
        capture_output=True, text=True,
    )
    ok = result.returncode == 0 and '"enabled": true' in result.stdout
    print(json.dumps({
        "installed": ok,
        "site_packages": site,
        "pth": os.path.join(site, PTH_NAME),
        "package": target_pkg,
        "options": options_path,
        "probe": result.stdout.strip() or result.stderr.strip(),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_uninstall(args) -> int:
    server = discover_server(args.server)
    require_private_server(server)
    _python, site = venv_paths(server)
    removed = []
    for path in (os.path.join(site, PTH_NAME), os.path.join(site, PKG_NAME)):
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(path)
    for cache in (os.path.join(site, "__pycache__"),):
        target = os.path.join(cache, "pdf2rea_engine.*.pyc")
        for path in _glob(target):
            os.remove(path)
            removed.append(path)
    keep = os.path.join(server, "config", "pdf2rea.json")
    print(json.dumps({"removed": removed,
                      "kept_options": keep if os.path.isfile(keep) else None,
                      "note": "删除 .pth 后 babeldoc 行为回到官方版本"},
                     ensure_ascii=False, indent=2))
    return 0


def _glob(pattern):
    import glob

    return glob.glob(pattern)


def cmd_status(args) -> int:
    server = discover_server(args.server)
    require_private_server(server)
    python, site = venv_paths(server)
    from pdf2rea_engine import options as options_module
    from pdf2rea_engine import run_ledger

    loaded, path = options_module.load()
    pth = os.path.join(site, PTH_NAME)
    pkg = os.path.join(site, PKG_NAME)
    from pdf2rea_engine import status as status_module

    directory = options_module.state_dir(path)
    last_run = status_module.aggregate(directory) or None
    if last_run:
        last_run = {key: last_run.get(key) for key in
                    ("run_id", "processes", "loaded_at", "finished_at",
                     "patches", "errors", "stats", "versions")}
    probe = subprocess.run(
        [python, "-c", "import importlib.util,sys;"
                       "print('yes' if importlib.util.find_spec('pdf2rea_engine') else 'no')"],
        capture_output=True, text=True,
    )
    print(json.dumps({
        "server": server,
        "venv_python": python,
        "site_packages": site,
        "pth_installed": os.path.isfile(pth),
        "package_installed": os.path.isdir(pkg),
        "importable_by_engine_python": probe.stdout.strip() == "yes",
        "options_file": path,
        "options": loaded,
        "ledger": run_ledger.stats(),
        "last_run": last_run,
        "state_dir": directory,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selftest(args) -> int:
    """Apply every patch against the real babeldoc and report what was hooked."""
    from pdf2rea_engine import apply, registry

    report = apply()
    import importlib

    for module in (
        "babeldoc.format.pdf.document_il.utils.layout_helper",
        "babeldoc.format.pdf.document_il.midend.paragraph_finder",
        "babeldoc.format.pdf.document_il.midend.styles_and_formulas",
        "babeldoc.format.pdf.document_il.midend.il_translator",
        "babeldoc.format.pdf.document_il.midend.il_translator_llm_only",
        "babeldoc.format.pdf.translation_config",
    ):
        try:
            importlib.import_module(module)
        except Exception as error:
            report.setdefault("import_failures", []).append(f"{module}: {error!r}")
    summary = registry.summary()
    report["patches"] = summary["applied"]
    report["errors"] = summary["errors"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    expected = 5
    if len(summary["applied"]) < expected:
        print(f"[SELFTEST] 只注册了 {len(summary['applied'])} 个补丁，少于预期 {expected}",
              file=sys.stderr)
        return 1
    return 0


def cmd_check(args) -> int:
    """Run the checker inside the engine's own interpreter."""
    server = discover_server(args.server)
    python, _site = venv_paths(server)
    result = subprocess.run([python, "-m", PKG_NAME, "selftest"],
                            capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pdf2rea_engine",
                                     description="PDF2REA 引擎补丁管理")
    parser.add_argument("--server", help="zotero-pdf2zh server 目录")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--force", action="store_true",
                         help="覆盖已安装的 pdf2rea_engine 并重写默认增强配置")
    install.set_defaults(func=cmd_install)
    sub.add_parser("uninstall").set_defaults(func=cmd_uninstall)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    sub.add_parser("check").set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
