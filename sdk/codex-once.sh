#!/bin/sh
set -eu
# SDK supplies "exec" first. Add lifecycle isolation without changing global config.
[ "${1:-}" = exec ] || exit 2
shift
exec "$JEV_CODEX_BIN" exec --ephemeral --ignore-user-config "$@"
