#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,11))' 2>/dev/null; then
    PY="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "需要 Python 3.11+。请让 Codex 检查已有 Python；本脚本不修改系统环境或自动安装软件。" >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
[ -e config.toml ] || cp config.example.toml config.toml
[ -e .env ] || cp .env.example .env
chmod 600 config.toml .env
# Runtime uses only the standard library; no pip download is needed.
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m jev_feishu demo
printf '\n离线检查完成。尚未连接飞书、发送消息或调用真实模型。请继续 CODEX_DEPLOY.md。\n'
