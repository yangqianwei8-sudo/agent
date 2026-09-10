#!/usr/bin/env bash
# Sealos DevBox — restart uvicorn on exit (long-running + auto-recovery).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PIDFILE="${ROOT}/data/uvicorn-supervisor.pid"
LOGFILE="${ROOT}/data/uvicorn-supervisor.log"
mkdir -p "${ROOT}/data"

if [[ -f "$PIDFILE" ]]; then
  old_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "supervisor already running pid=$old_pid"
    exit 0
  fi
fi

echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

if [[ -x "$ROOT/deploy/configure-git-auth.sh" ]]; then
  "$ROOT/deploy/configure-git-auth.sh" >/dev/null 2>&1 || true
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"

echo "[$(date -Is)] supervisor started root=$ROOT host=$HOST port=$PORT" >> "$LOGFILE"

while true; do
  echo "[$(date -Is)] starting uvicorn" >> "$LOGFILE"
  uvicorn backend.main:app --host "$HOST" --port "$PORT" >> "$LOGFILE" 2>&1 || true
  echo "[$(date -Is)] uvicorn exited — restarting in 2s" >> "$LOGFILE"
  sleep 2
done
