#!/usr/bin/env bash
# Airlock — one command. Runs fully offline (no keys needed).
#
#   ./run.sh                       # local runner + built frontend at http://127.0.0.1:8770
#
# Upgrade paths (put keys in ./.env or export them first):
#   DAYTONA_API_KEY=...            # experiments run in a pool of Daytona sandboxes
#   AIRLOCK_RUNNER=local           # force the local pool even when a key is present
#   AIRLOCK_DAYTONA_POOL=4         # sandboxes in the pool
#   AIRLOCK_PORT=8770
set -euo pipefail
cd "$(dirname "$0")"
PORT="${AIRLOCK_PORT:-8770}"

# Prefer the project venv (daytona SDK + scikit-learn) when it exists.
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

# Build the frontend once if web/dist is missing (dev: `cd web && npm run dev`).
if [ ! -f web/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "  building web/dist…"
    ( cd web && { [ -d node_modules ] || npm ci --silent || npm install --silent; } && npm run build --silent )
  else
    echo "  npm not found; serving the legacy console instead of web/dist" >&2
  fi
fi

"$PY" -m airlock &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT INT TERM
sleep 1
URL="http://127.0.0.1:${PORT}"
command -v open >/dev/null 2>&1 && open "$URL" || echo "open $URL"
wait $PID
