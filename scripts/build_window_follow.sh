#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || exit 1
xcrun --find swiftc >/dev/null
umask 077
xcrun swiftc -O -framework AppKit -framework ApplicationServices native/WindowFollow.swift -o native/jev-window-follow
chmod 700 native/jev-window-follow
