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

VENV_PYTHON="$ROOT/.venv/bin/python"
VENV_UVICORN="$ROOT/.venv/bin/uvicorn"
if [[ ! -x "$VENV_PYTHON" || ! -x "$VENV_UVICORN" ]]; then
  echo "ERROR: project venv required at $ROOT/.venv (python>=3.11)" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

if [[ -x "$ROOT/deploy/configure-git-auth.sh" ]]; then
  "$ROOT/deploy/configure-git-auth.sh" >/dev/null 2>&1 || true
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"

_terminate_stale_uvicorn() {
  # Acceptance scripts or manual runs may leave 127.0.0.1:8000 bound, blocking public 0.0.0.0.
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  fi
  pkill -f "uvicorn backend.main:app" >/dev/null 2>&1 || true
  sleep 1
}

echo "[$(date -Is)] supervisor started root=$ROOT host=$HOST port=$PORT" >> "$LOGFILE"

while true; do
  _terminate_stale_uvicorn
  if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
  fi
  echo "[$(date -Is)] starting uvicorn git_sha=${GIT_SHA:-unknown}" >> "$LOGFILE"
  "$VENV_UVICORN" backend.main:app --host "$HOST" --port "$PORT" >> "$LOGFILE" 2>&1 || true
  echo "[$(date -Is)] uvicorn exited — restarting in 2s" >> "$LOGFILE"
  sleep 2
done
