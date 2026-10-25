#!/usr/bin/env python3
"""Private server compatibility fixes; upstream server and engine files stay intact."""
from __future__ import annotations

import argparse
import ast
import functools
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

OWNER = "pdf2rea@qoder.dev"
COMPAT_VERSION = "1.1.2"
OUTPUT_TYPES = frozenset({"origin-cut", "mono", "dual", "mono-cut", "dual-cut", "crop-compare", "compare"})


def safe_output_path(inpath: str, outtype: str, engine: str) -> str:
    """Change a terminal PDF product suffix, never a directory or internal '.pdf'."""
    original = os.fspath(inpath)
    directory, name = os.path.split(original)
    stem, extension = os.path.splitext(name)
    if not name or extension.lower() != ".pdf" or outtype not in OUTPUT_TYPES:
        raise ValueError("无法生成 PDF 输出路径：文件类型或输出类型无效")
    separator = "." if engine == "pdf2zh_next" else "-"
    if stem.lower().endswith((".lr_dual", ".tb_dual")):
        output_name = stem[:-len(".LR_dual")] + separator + outtype + ".pdf"
    else:
        # Retain the existing product separator for both legacy -mono.pdf and
        # Next .mono.pdf names. A word such as 'shortcut.pdf' is an original.
        product = re.fullmatch(r"(.*[.\-]|)(mono-cut|dual-cut|crop-compare|origin-cut|compare|mono|dual|cut)", stem, re.IGNORECASE)
        if product:
            output_name = product.group(1) + outtype + ".pdf"
        else:
            suffix = "cut" if outtype == "origin-cut" else outtype
            output_name = stem + separator + suffix + ".pdf"
    result = os.path.join(directory, output_name)
    if os.path.dirname(result) != directory or os.path.normcase(os.path.abspath(result)) == os.path.normcase(os.path.abspath(original)):
        raise ValueError("拒绝覆盖输入文件或更改 PDF 输出目录")
    return result


def install_compat(translator_class: type) -> None:
    """Install the narrow method fixes before PDFTranslator constructs its routes."""
    if getattr(translator_class, "_zhiyi_compat_version", None) == COMPAT_VERSION:
        return
    if not callable(getattr(translator_class, "get_filename_after_process", None)) or not callable(getattr(translator_class, "health_check", None)):
        raise RuntimeError("独立服务接口已变化，无法安全安装路径兼容层")

    def get_filename_after_process(self: Any, inpath: str, outtype: str, engine: str) -> str:
        return safe_output_path(inpath, outtype, engine)

    original_health = translator_class.health_check

    @functools.wraps(original_health)
    def health_check(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_health(self, *args, **kwargs)
        response = result[0] if isinstance(result, tuple) else result
        payload = response.get_json() if hasattr(response, "get_json") else response
        if not isinstance(payload, dict):
            raise RuntimeError("独立服务健康检查格式已变化")
        payload = dict(payload, zhiyiCompatVersion=COMPAT_VERSION, pid=os.getpid())
        if isinstance(response, dict):
            if isinstance(result, tuple):
                return (payload, *result[1:])
            return payload
        response.set_data(json.dumps(payload, ensure_ascii=False))
        return result

    translator_class.get_filename_after_process = get_filename_after_process
    translator_class.health_check = health_check
    translator_class._zhiyi_compat_version = COMPAT_VERSION


def compatibility_tree(source: str, filename: str = "server.py") -> ast.Module:
    """Inject one runtime hook into the original module, retaining its main block."""
    tree = ast.parse(source, filename=filename)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PDFTranslator"]
    if len(classes) != 1:
        raise RuntimeError("独立服务需包含唯一的 PDFTranslator 类，未执行服务代码")
    target = classes[0]
    methods = {node.name for node in target.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not {"get_filename_after_process", "health_check"} <= methods:
        raise RuntimeError("独立服务类接口已变化，未执行服务代码")
    injection = ast.Expr(value=ast.Call(func=ast.Name(id="_zhiyi_install_compat", ctx=ast.Load()), args=[ast.Name(id="PDFTranslator", ctx=ast.Load())], keywords=[]))
    ast.copy_location(injection, target)
    tree.body.insert(tree.body.index(target) + 1, injection)
    return ast.fix_missing_locations(tree)


def validate_runtime(runtime: Path) -> Path:
    runtime = runtime.expanduser().absolute()
    for path in (runtime, runtime / "pdf2rea-runtime.json", runtime / "server.py", runtime / "zotero-pdf2zh-next-venv"):
        if path.is_symlink():
            raise ValueError("独立服务不能使用原版文件或目录的符号链接")
    marker = json.loads((runtime / "pdf2rea-runtime.json").read_text(encoding="utf-8"))
    if marker.get("owner") != OWNER:
        raise ValueError("拒绝启动未标记的知译 PDF 独立环境")
    if int(marker.get("server_port", 8891)) == 8890:
        raise ValueError("知译 PDF 独立服务不能使用原版 8890 端口")
    return runtime


def execute_server(runtime: Path, argv: list[str]) -> None:
    runtime = validate_runtime(runtime)
    server_file = runtime / "server.py"
    tree = compatibility_tree(server_file.read_text(encoding="utf-8"), str(server_file))
    marker = json.loads((runtime / "pdf2rea-runtime.json").read_text(encoding="utf-8"))
    protected = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    protected.add_argument("--port", type=int)
    protected.add_argument("--host")
    overrides, forwarded = protected.parse_known_args(argv)
    port = int(marker.get("server_port", 8891))
    if overrides.port is not None and overrides.port != port:
        raise ValueError("独立服务端口必须与环境标记一致")
    if overrides.host is not None and overrides.host != "127.0.0.1":
        raise ValueError("独立服务只允许绑定 127.0.0.1")
    os.environ["PATH"] = str(runtime / "zotero-pdf2zh-next-venv/bin") + os.pathsep + os.environ.get("PATH", "")
    for variable, relative in {
        "PDF2REA_CONFIG": "config/pdf2rea.json",
        "PDF2REA_CACHE_DIR": "cache/babeldoc",
        "PDF2REA_TRANSLATOR_CACHE": "cache/pdf2zh_next",
        "PDF2REA_CONFIG_DIR": "config/pdf2zh",
        "PDF2REA_LEDGER_PATH": "ledger.json",
    }.items():
        os.environ[variable] = str(runtime / relative)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.pop("PYTHONPATH", None)
    sys.dont_write_bytecode = True
    os.chdir(runtime)
    sys.path.insert(0, str(runtime))
    # Keep the upstream __main__ intact, including argument parsing, config
    # migration and global `args` referenced by the request handlers.
    # Protected arguments go last so even an upstream abbreviated option cannot
    # override the marked port, loopback binding or private environment mode.
    sys.argv = [str(server_file), *forwarded, "--port", str(port), "--host", "127.0.0.1", "--check_update=False", "--enable_venv=False"]
    namespace = {"__name__": "__main__", "__file__": str(server_file), "__package__": None, "_zhiyi_install_compat": install_compat}
    exec(compile(tree, str(server_file), "exec"), namespace)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime", type=Path, required=True)
    options, server_argv = parser.parse_known_args()
    execute_server(options.runtime, server_argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
