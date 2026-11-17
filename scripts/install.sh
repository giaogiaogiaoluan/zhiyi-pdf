#!/usr/bin/env bash
# 安装或升级知译 PDF 引擎到 ~/.zhiyi（可用 ZHIYI_HOME 改位置），并启动本机服务。
#
#   bash scripts/install.sh            # 安装 / 升级并启动服务
#   bash scripts/install.sh --no-start # 只安装
#
# 只写入 ~/.zhiyi；不会改动 PDF2zh、知译 1.x（~/.pdf2rea）或系统 Python。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${ZHIYI_HOME:-$HOME/.zhiyi}"
VENV="$HOME_DIR/venv"
PY="$VENV/bin/python"
START=1
[[ "${1:-}" == "--no-start" ]] && START=0

mkdir -p "$HOME_DIR"
echo "==> 引擎位置：$HOME_DIR"

if command -v uv >/dev/null 2>&1; then
  [[ -x "$PY" ]] || uv venv --quiet --python 3.12 "$VENV"
  echo "==> 安装依赖（uv）"
  uv pip install --quiet --python "$PY" --upgrade "$ROOT/engine"
  uv pip install --quiet --python "$PY" --reinstall --no-deps "$ROOT/engine"
else
  BASE="$(command -v python3 || true)"
  [[ -n "$BASE" ]] || { echo "需要 Python 3.10 及以上版本（或安装 uv：https://docs.astral.sh/uv/）" >&2; exit 1; }
  "$BASE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "当前 python3 版本过低，需要 3.10 及以上" >&2; exit 1; }
  [[ -x "$PY" ]] || "$BASE" -m venv "$VENV"
  echo "==> 安装依赖（pip）"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet --upgrade "$ROOT/engine"
  "$PY" -m pip install --quiet --force-reinstall --no-deps "$ROOT/engine"
fi
rm -rf "$ROOT/engine/build" "$ROOT"/engine/*.egg-info

echo "==> 准备版面模型与字体（首次约 300 MB；已用过 PDF2zh 的机器会直接复用缓存）"
"$PY" -m zhiyi service prepare >/dev/null || {
  echo "模型或字体下载失败，请检查网络后重新运行本脚本" >&2; exit 1; }

echo "==> 引擎版本：$("$PY" -m zhiyi --version)"
if [[ $START -eq 1 ]]; then
  "$PY" -m zhiyi service stop >/dev/null || true
  "$PY" -m zhiyi service start
fi
echo
echo "完成。在 Zotero 中右键选择“知译 PDF：翻译”即可使用；服务未运行时插件会自动启动它。"
