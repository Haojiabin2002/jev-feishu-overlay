#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo '请先执行 bash scripts/bootstrap_overlay.sh。离线演示不需要模型密钥或飞书授权。'
  exit 1
fi
exec .venv/bin/python -m jev_feishu overlay --demo
