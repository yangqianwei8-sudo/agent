#!/usr/bin/env bash
# Sealos DevBox / App Launchpad entrypoint — bind 0.0.0.0 for public ingress.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export APP_HOST="${APP_HOST:-0.0.0.0}"
export APP_PORT="${APP_PORT:-8000}"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

if [[ -x "$ROOT/deploy/configure-git-auth.sh" ]]; then
  "$ROOT/deploy/configure-git-auth.sh" >/dev/null 2>&1 || true
fi

exec "$ROOT/.venv/bin/uvicorn" backend.main:app --host "$APP_HOST" --port "$APP_PORT"
