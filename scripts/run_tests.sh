#!/usr/bin/env bash
# Run source/contract tests, then integration tests in a separate engine environment.
#
#   scripts/run_tests.sh                         # includes real BabelDOC wiring
#   scripts/run_tests.sh --skip-wiring           # source/contract checks only
#   VENV_PY=/path/to/venv/bin/python scripts/run_tests.sh
#   VENV=/path/to/venv scripts/run_tests.sh --e2e [--pdf /path/to/paper.pdf --pages 1-6]
#
# VENV also accepts an interpreter path for compatibility with earlier releases.
# Test state defaults to a private temporary directory, never the running server.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "用法：scripts/run_tests.sh [--skip-wiring] [--e2e [--pdf 文件 --pages 页码]]"
  echo "VENV_PY 或 VENV 可指定独立引擎环境；默认查找 ~/.pdf2rea/server/zotero-pdf2zh-next-venv。"
}

SKIP_WIRING=0
RUN_E2E=0
E2E_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-wiring) SKIP_WIRING=1; shift ;;
    --e2e) RUN_E2E=1; shift ;;
    --pdf|--pages)
      [[ $# -ge 2 ]] || { echo "缺少 $1 的值。" >&2; exit 2; }
      E2E_ARGS+=("$1" "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ $SKIP_WIRING -eq 1 && $RUN_E2E -eq 1 ]]; then
  echo "--e2e 需要真实引擎环境，不能与 --skip-wiring 同用。" >&2
  exit 2
fi
if [[ ${#E2E_ARGS[@]} -gt 0 && $RUN_E2E -eq 0 ]]; then
  echo "--pdf / --pages 只能用于 --e2e。" >&2
  exit 2
fi
for tool in python3 node; do
  command -v "$tool" >/dev/null 2>&1 || { echo "缺少必需工具：$tool" >&2; exit 1; }
done

ENGINE_PY="${VENV_PY:-${VENV:-${HOME}/.pdf2rea/server/zotero-pdf2zh-next-venv/bin/python}}"
[[ -d "$ENGINE_PY" ]] && ENGINE_PY="$ENGINE_PY/bin/python"
if [[ "$ENGINE_PY" != */* ]]; then
  ENGINE_PY="$(command -v "$ENGINE_PY" || true)"
fi

TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"
TEST_ROOT="$(mktemp -d "$TEMP_BASE/zhiyi-pdf-tests.XXXXXX")"
TEST_ROOT="$(cd "$TEST_ROOT" && pwd -P)"
cleanup() {
  if [[ "${KEEP_TEST_ARTIFACTS:-0}" == "1" ]]; then
    echo "测试临时文件保留于：$TEST_ROOT"
  else
    rm -rf "$TEST_ROOT"
  fi
}
trap cleanup EXIT
export PDF2REA_CACHE_DIR="${PDF2REA_CACHE_DIR:-$TEST_ROOT/cache/babeldoc}"
export PDF2REA_TRANSLATOR_CACHE="${PDF2REA_TRANSLATOR_CACHE:-$TEST_ROOT/cache/pdf2zh_next}"
export PDF2REA_CONFIG_DIR="${PDF2REA_CONFIG_DIR:-$TEST_ROOT/config/pdf2zh}"
export PDF2REA_LEDGER_PATH="${PDF2REA_LEDGER_PATH:-$TEST_ROOT/ledger.json}"
# Do not inherit the config or activation marker of a running translation job.
export PDF2REA_CONFIG="$TEST_ROOT/pdf2rea.json"
printf '{}\n' > "$PDF2REA_CONFIG"
unset PDF2REA_APPLY_EAGERLY PDF2REA_ENGINE_ACTIVE PDF2REA_DISABLE
export PYTHONDONTWRITEBYTECODE=1
# Select this checkout before any installed .pth imports the engine package.
export PYTHONPATH="$PWD/engine${PYTHONPATH:+:$PYTHONPATH}"
CONTRACT_DIR="${CONTRACT_DIR:-$TEST_ROOT/contract}"
mkdir -p "$CONTRACT_DIR"
CONTRACT_DIR="$(cd "$CONTRACT_DIR" && pwd -P)"

echo "==> 纯逻辑测试（python3）"
python3 tests/test_engine.py
python3 tests/test_joint_paragraph.py
python3 tests/test_prompt_boundary.py
python3 tests/test_preferences_contract.py
python3 tests/test_server_compat.py

echo
echo "==> 插件请求与配置契约（模拟 Zotero）"
node tests/test_plugin_contract.mjs "$CONTRACT_DIR"
python3 tests/test_plugin_contract_engine.py "$CONTRACT_DIR"

if [[ $SKIP_WIRING -eq 1 ]]; then
  echo
  echo "仅完成纯逻辑和契约测试；已按 --skip-wiring 跳过真实引擎接线测试，未验证 BabelDOC 集成。"
  exit 0
fi
if [[ ! -x "$ENGINE_PY" ]]; then
  echo >&2
  echo "真实引擎接线测试未验证：找不到可执行的独立引擎 Python：${ENGINE_PY:-<空>}" >&2
  echo "请设置 VENV_PY=/path/to/venv/bin/python，或明确使用 --skip-wiring 仅运行纯逻辑检查。" >&2
  exit 1
fi

echo
echo "==> 真实 BabelDOC 接线、排版和多进程测试"
"$ENGINE_PY" tests/test_patch_wiring.py
"$ENGINE_PY" tests/test_joint_wiring.py
"$ENGINE_PY" tests/test_layout_repair.py
"$ENGINE_PY" tests/test_structured_text.py
"$ENGINE_PY" tests/run_spawn_test.py

if [[ $RUN_E2E -eq 1 ]]; then
  # Preserve PDF outputs for inspection; cache/config/ledger still use TEST_ROOT.
  E2E_WORKDIR="$(mktemp -d "$TEMP_BASE/zhiyi-pdf-e2e.XXXXXX")"
  echo
  echo "==> 离线端到端对照（原引擎 / 启用增强，各生成一次 PDF）"
  echo "    输出保留于：$E2E_WORKDIR"
  if [[ ${#E2E_ARGS[@]} -gt 0 ]]; then
    "$ENGINE_PY" tests/e2e_compare.py "$E2E_WORKDIR" "${E2E_ARGS[@]}"
  else
    "$ENGINE_PY" tests/e2e_compare.py "$E2E_WORKDIR"
  fi
fi
