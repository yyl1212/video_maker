#!/bin/sh
# 不source .env；交给Python按dotenv数据解析，避免执行配置中的shell内容。
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname -- "$SCRIPT_DIR")
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo '缺少后端环境：请先在 backend 执行 uv sync --locked。' >&2
  exit 1
fi
exec "$ROOT/backend/.venv/bin/python" "$SCRIPT_DIR/local_runtime.py" "$@"
