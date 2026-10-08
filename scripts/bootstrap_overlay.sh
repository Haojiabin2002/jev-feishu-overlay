#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
PY=""
if [ -n "${PYTHON_BIN:-}" ]; then
  "$PYTHON_BIN" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))'
  PY="$PYTHON_BIN"
else
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))' 2>/dev/null; then
      PY="$(command -v "$candidate")"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo '需要自带 tkinter 的 Python 3.11+；请让 Codex 检查已有环境。本脚本不安装系统软件。' >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then "$PY" -m venv .venv; fi
.venv/bin/python -c 'import sys, tkinter; raise SystemExit(sys.version_info < (3,11))'
for pair in 'config.example.toml:config.toml' 'overlay.example.toml:overlay.toml' '.env.example:.env'; do
  src="${pair%%:*}"; dest="${pair#*:}"
  [ -e "$dest" ] || cp "$src" "$dest"
  chmod 600 "$dest"
done
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_overlay -p test_overlay_core.py -v
.venv/bin/python -m unittest discover -s tests_overlay -p test_delivery.py -v
# Requires an actual desktop; headless Linux exits with explicit skips, not GUI success.
.venv/bin/python -m unittest discover -s tests_overlay -p test_overlay_ui.py -v
.venv/bin/python -m unittest discover -s tests_overlay -p test_ux_round.py -v
printf '\n离线检查结束。没有访问飞书或模型，没有安装登录项。按 CODEX_DEPLOY.md 继续。\n'
