#!/bin/zsh
set -eu
TASK_PROJECT="${0:A:h}"
TASK_RUNTIME="$HOME/.pdf2rea/server"
if [[ ! -f "$TASK_RUNTIME/pdf2rea-runtime.json" ]]; then
  python3 "$TASK_PROJECT/scripts/setup_runtime.py"
fi
exec "$TASK_RUNTIME/zotero-pdf2zh-next-venv/bin/python" -B "$TASK_PROJECT/plugin/content/runtime/service_control.py" start --runtime "$TASK_RUNTIME"
