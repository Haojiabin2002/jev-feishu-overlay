#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo '请先执行 bash scripts/bootstrap_native.sh 并完成 CODEX_DEPLOY.md。'
  exit 1
fi
exec .venv/bin/python -m jev_feishu --config "$PWD/config.toml" overlay
