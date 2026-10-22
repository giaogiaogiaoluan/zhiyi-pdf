#!/usr/bin/env bash
# Remove the PDF2REA patch layer. Leaves pdf2rea.json, the glossary CSV, the status
# marker and ~/.pdf2rea/ledger.json alone -- delete those by hand if you want a clean slate.
#
#   scripts/uninstall_engine.sh [path/to/zotero-pdf2zh/server]
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${1:-${HOME}/.pdf2rea/server}"
VENV="${SERVER}/zotero-pdf2zh-next-venv"
[[ -x "${VENV}/bin/python" ]] || { echo "找不到引擎 venv：${VENV}" >&2; exit 1; }

PYTHONPATH="$PWD/engine" "${VENV}/bin/python" -m pdf2rea_engine \
  --server "$SERVER" uninstall

echo
echo "验证：引擎解释器里应该已经找不到 pdf2rea_engine"
"${VENV}/bin/python" - <<'PY'
import importlib.util
print("pdf2rea_engine importable:", importlib.util.find_spec("pdf2rea_engine") is not None)
PY
