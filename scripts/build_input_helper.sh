#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(uname -s)" = "Darwin" ] || { echo '只能在 Mac 上编译输入助手。' >&2; exit 1; }
command -v xcrun >/dev/null || { echo '需要 Apple Command Line Tools；本脚本不会擅自安装。'; exit 1; }
xcrun --find swiftc >/dev/null
umask 077
xcrun swiftc -O -framework AppKit -framework ApplicationServices native/InputHelper.swift -o native/jev-input-helper
chmod 700 native/jev-input-helper
printf '{"action":"probe"}' | native/jev-input-helper
printf '\n编译完成；未授予辅助功能权限，未写入飞书。继续按部署执行单测试。\n'
