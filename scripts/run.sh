#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGFILE="/tmp/inkobold-launch.log"

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

exec >>"$LOGFILE" 2>&1
echo "---- $(date -Iseconds) launch ----"

cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Creating venv…"
  python3 -m venv --system-site-packages "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
fi

export INKOBOLD_ICON="$ROOT/scripts/inkobold.png"
exec "$ROOT/.venv/bin/python" -m inkobold "$@"
