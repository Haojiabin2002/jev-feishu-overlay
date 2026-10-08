#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/python scripts/set_secrets.py --jev-only
printf "\n完成后可关闭此终端窗口。\n"
