#!/usr/bin/env bash
# 运行全部测试：引擎（pytest，合成 PDF，无需联网与模型）和插件（node）。
#   PYTHON=/path/to/python bash scripts/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in .venv/bin/python "$HOME/.zhiyi/venv/bin/python" python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import pymupdf, pytest' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  done
fi
[[ -n "$PYTHON" ]] || { echo "找不到装有 pymupdf 与 pytest 的 Python，请用 PYTHON=… 指定" >&2; exit 1; }
echo "==> 引擎测试（$PYTHON）"
PYTHONPATH=engine PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest engine/tests -q -W ignore::DeprecationWarning
echo "==> 插件测试"
node --check plugin/bootstrap.js
node --check plugin/content/scripts/pdf2rea.js
node --test plugin/tests/*.test.mjs
