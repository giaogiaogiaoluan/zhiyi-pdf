#!/usr/bin/env bash
# Build dist/ZhiYi-PDF-<version>.xpi from plugin/ + the bundled engine component.
#
#   scripts/build_xpi.sh                    # includes real-engine wiring tests
#   scripts/build_xpi.sh --skip-wiring      # source/contract checks only
#   VENV_PY=/path/to/venv/bin/python scripts/build_xpi.sh
#
# --skip-wiring never skips logic, contract, syntax or package validation.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
STAGE="$ROOT/.build/plugin"
SKIP_WIRING=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-wiring) SKIP_WIRING=1; shift ;;
    --skip-tests)
      echo "提示：旧参数 --skip-tests 仅等同于 --skip-wiring；纯逻辑、契约和打包校验仍会运行。" >&2
      SKIP_WIRING=1; shift ;;
    -h|--help)
      echo "用法：scripts/build_xpi.sh [--skip-wiring]"
      echo "VENV_PY 或 VENV 可指定独立引擎环境；缺少引擎环境时请明确使用 --skip-wiring。"
      exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
for tool in python3 node rsync; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少必需工具：$tool" >&2; exit 1; }
done
for notice in LICENSE THIRD_PARTY_NOTICES.md; do
  [[ -f "$ROOT/$notice" ]] || { echo "缺少发布许可文件：$notice" >&2; exit 1; }
done

export PYTHONDONTWRITEBYTECODE=1
version="$(python3 -c 'import json;print(json.load(open("plugin/manifest.json"))["version"])')"
xpi="dist/ZhiYi-PDF-${version}.xpi"

echo "==> 为知译 PDF 打包翻译增强组件"
# Keep source and staging bytes identical so validate.py inspects shipped files.
rm -rf plugin/content/engine
mkdir -p plugin/content/engine
rsync -a --exclude '.DS_Store' --exclude '__pycache__' --exclude '*.pyc' \
  engine/pdf2rea_engine/ plugin/content/engine/pdf2rea_engine/

python3 - "$ROOT/plugin/content/engine" <<'PY'
import json
import sys
from pathlib import Path

engine = Path(sys.argv[1])
files = []
for path in sorted(engine.rglob("*")):
    if not path.is_file() or path.name == ".DS_Store" or "__pycache__" in path.parts:
        continue
    rel = str(path.relative_to(engine))
    files.append({
        "path": rel,
        "bytes": path.stat().st_size,
        "kind": "csv" if rel.endswith(".csv") else "python",
    })
manifest = {"version": 1, "package": "pdf2rea_engine", "files": files}
(engine / "engine_manifest.json").write_text(
    json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"    engine_manifest.json: {len(files)} files")
PY

# The resolution-independent SVG also satisfies Zotero's @0.5x icon lookup.
cp plugin/content/icons/favicon.svg plugin/content/icons/favicon@0.5x.svg

echo "==> 测试"
if [[ $SKIP_WIRING -eq 1 ]]; then
  bash scripts/run_tests.sh --skip-wiring
else
  bash scripts/run_tests.sh
fi

echo "==> 语法与源文件校验"
node --check plugin/content/scripts/pdf2rea.js
node --check plugin/bootstrap.js
python3 -m compileall -q plugin/content/engine >/dev/null
find plugin/content/engine -name '__pycache__' -type d -prune -exec rm -rf {} +
python3 scripts/validate.py

echo "==> 整理安装包文件"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R plugin/. "$STAGE/"
# Distribute license and upstream attribution with the installed code as well.
cp "$ROOT/LICENSE" "$ROOT/THIRD_PARTY_NOTICES.md" "$STAGE/"
find "$STAGE" -name '.DS_Store' -delete
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "==> 打包 $xpi"
rm -f "$xpi"
# Reproducible ZIP; native Zotero installation remains a separate release check.
python3 scripts/pack_xpi.py "$STAGE" "$ROOT/$xpi"
python3 scripts/validate.py --xpi "$xpi"

echo
echo "已生成：$xpi"
if [[ $SKIP_WIRING -eq 1 ]]; then
  echo "此构建未验证真实引擎集成（--skip-wiring）；请在正式发布前另行完成接线和 Zotero 安装检查。"
fi
echo "安装：Zotero 插件管理器 → 齿轮图标 → 从文件安装插件…"
echo "组件状态：Zotero 设置 → 知译 PDF → 连接与诊断"
