#!/usr/bin/env bash
# Sealos DevBox — supervise uvicorn with process + health recovery.
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
HEALTH_URL="${AUTONOMOUS_HEALTH_URL:-http://127.0.0.1:${PORT}/healthz}"
HEALTH_INTERVAL="${AUTONOMOUS_HEALTH_INTERVAL_SECONDS:-10}"
HEALTH_FAILURE_LIMIT="${AUTONOMOUS_HEALTH_FAILURE_LIMIT:-3}"
STARTUP_GRACE="${AUTONOMOUS_STARTUP_GRACE_SECONDS:-15}"

_ensure_external_watchdog() {
  command -v crontab >/dev/null 2>&1 || return 0
  local marker="# litigation-agent-supervisor-watchdog"
  local current
  current="$(crontab -l 2>/dev/null || true)"
  grep -qF "$marker" <<<"$current" && return 0
  {
    printf '%s\n' "$current"
    printf '%s\n' "$marker"
    printf '@reboot nohup %q >/dev/null 2>&1 &\n' "$ROOT/deploy/supervise-uvicorn.sh"
    printf '* * * * * pgrep -f %q >/dev/null 2>&1 || nohup %q >/dev/null 2>&1 &\n' \
      "$ROOT/deploy/supervise-uvicorn.sh" "$ROOT/deploy/supervise-uvicorn.sh"
  } | crontab - || true
}

_terminate_stale_uvicorn() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  fi
  pkill -f "uvicorn backend.main:app" >/dev/null 2>&1 || true
  sleep 1
}

_health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl --silent --show-error --fail --max-time 4 "$HEALTH_URL" >/dev/null 2>&1
    return $?
  fi
  "$VENV_PYTHON" - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=4) as r:
    raise SystemExit(0 if 200 <= r.status < 300 else 1)
PY
}

_stop_child() {
  local child_pid="$1"
  if kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      kill -0 "$child_pid" 2>/dev/null || return 0
      sleep 1
    done
    kill -KILL "$child_pid" 2>/dev/null || true
  fi
}

_ensure_external_watchdog

echo "[$(date -Is)] supervisor started root=$ROOT host=$HOST port=$PORT" >> "$LOGFILE"

while true; do
  _terminate_stale_uvicorn
  if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
  fi

  echo "[$(date -Is)] starting uvicorn git_sha=${GIT_SHA:-unknown}" >> "$LOGFILE"
  "$VENV_UVICORN" backend.main:app --host "$HOST" --port "$PORT" >> "$LOGFILE" 2>&1 &
  child_pid=$!
  failures=0
  started_at=$(date +%s)

  while kill -0 "$child_pid" 2>/dev/null; do
    sleep "$HEALTH_INTERVAL"
    now=$(date +%s)
    if (( now - started_at < STARTUP_GRACE )); then
      continue
    fi

    if _health_ok; then
      failures=0
    else
      failures=$((failures + 1))
      echo "[$(date -Is)] health check failed count=$failures url=$HEALTH_URL child=$child_pid" >> "$LOGFILE"
      if (( failures >= HEALTH_FAILURE_LIMIT )); then
        echo "[$(date -Is)] health failure threshold reached; restarting uvicorn child=$child_pid" >> "$LOGFILE"
        _stop_child "$child_pid"
        break
      fi
    fi
  done

  wait "$child_pid" 2>/dev/null || true
  echo "[$(date -Is)] uvicorn exited/unhealthy — restarting in 2s" >> "$LOGFILE"
  sleep 2
done
