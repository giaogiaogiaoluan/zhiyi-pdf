#!/usr/bin/env bash
# 构建 dist/ZhiYi-PDF-<版本>.xpi（只含 Zotero 插件；引擎用 scripts/install.sh 安装）。
#   bash scripts/build_xpi.sh [--skip-tests]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
STAGE="$ROOT/.build/plugin"
[[ "${1:-}" == "--skip-tests" ]] || bash scripts/run_tests.sh

version="$(python3 -c 'import json;print(json.load(open("plugin/manifest.json"))["version"])')"
engine_version="$(python3 -c 'import re;print(re.search(r"__version__ = \"(.+?)\"", open("engine/zhiyi/__init__.py").read()).group(1))')"
plugin_version="$(sed -n 's/.*PLUGIN_VERSION = "\(.*\)";/\1/p' plugin/content/scripts/pdf2rea.js)"
if [[ "$version" != "$engine_version" || "$version" != "$plugin_version" ]]; then
  echo "版本不一致：manifest $version / 引擎 $engine_version / 脚本 $plugin_version" >&2
  exit 1
fi

xpi="dist/ZhiYi-PDF-${version}.xpi"
echo "==> 打包 $xpi"
cp plugin/content/icons/favicon.svg plugin/content/icons/favicon@0.5x.svg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R plugin/manifest.json plugin/bootstrap.js plugin/prefs.js plugin/content "$STAGE/"
cp LICENSE THIRD_PARTY_NOTICES.md "$STAGE/"
find "$STAGE" -name '.DS_Store' -delete
rm -f "$xpi"
python3 scripts/pack_xpi.py "$STAGE" "$ROOT/$xpi"
echo "已生成：$xpi"
echo "安装：Zotero → 工具 → 插件 → 齿轮 → 从文件安装插件…"
