#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
xcrun swiftc -O native/MessageAnchors.swift -o native/jev-message-anchors
chmod 700 native/jev-message-anchors
