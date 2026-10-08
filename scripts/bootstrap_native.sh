#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
if [ ! -x .venv/bin/python ]; then
  PY="${PYTHON_BIN:-python3.11}"
  "$PY" -c 'import sys; raise SystemExit(sys.version_info < (3,11))'
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements-native.txt
npm ci --prefix sdk --ignore-scripts --omit=optional
for pair in 'config.example.toml:config.toml' 'overlay.example.toml:overlay.toml' '.env.example:.env'; do
  src="${pair%%:*}"; dest="${pair#*:}"
  [ -e "$dest" ] || cp "$src" "$dest"
  chmod 600 "$dest"
done
bash scripts/build_message_anchors.sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_native -v
node --test sdk/*.test.mjs
printf '\n原生依赖和后台测试完成；未改飞书绑定，未读取聊天或调用模型，未打开前台，未安装登录项。\n'
