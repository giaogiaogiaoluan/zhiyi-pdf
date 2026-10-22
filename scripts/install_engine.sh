#!/usr/bin/env bash
# Install (or update) the PDF2REA patch layer in the local zotero-pdf2zh engine venv,
# without going through Zotero. Equivalent to the plugin's "安装/更新补丁" button.
#
#   scripts/install_engine.sh                       # default server dir
#   scripts/install_engine.sh --force               # overwrite an existing install
#   scripts/install_engine.sh --dry-run
#   scripts/install_engine.sh /path/to/server       # explicit server dir
set -euo pipefail
cd "$(dirname "$0")/.."

SERVER="${HOME}/.pdf2rea/server"
DRY=""
FORCE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY="1" ;;
    --force)   FORCE="--force" ;;
    -*)        echo "unknown option: $arg" >&2; exit 2 ;;
    *)         SERVER="$arg" ;;
  esac
done

VENV="${SERVER}/zotero-pdf2zh-next-venv"
[[ -x "${VENV}/bin/python" ]] || { echo "找不到引擎 venv：${VENV}" >&2; exit 1; }

run() { PYTHONPATH="$PWD/engine" "${VENV}/bin/python" -m pdf2rea_engine --server "$SERVER" "$@"; }

# The CLI resolves paths relative to the server dir, so cd there for file operations.
if [[ -n "$DRY" ]]; then
  run --dry-run install
  exit 0
fi
run install $FORCE
echo
echo "==> 在引擎解释器里自检（补丁是否真能挂到 babeldoc 上）"
run check
