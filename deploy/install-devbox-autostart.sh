#!/usr/bin/env bash
# Install DevBox autonomous runtime autostart + external supervisor watchdog.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# litigation-agent-autostart"
PROFILE="${HOME}/.profile"
SUPERVISOR="$ROOT/deploy/supervise-uvicorn.sh"

chmod +x "$SUPERVISOR" "$ROOT/deploy/configure-git-auth.sh" "$ROOT/deploy/load-env.sh" 2>/dev/null || true

# Interactive/login-shell fallback.
if ! grep -q "$MARKER" "$PROFILE" 2>/dev/null; then
  cat >> "$PROFILE" <<EOF

$MARKER
if [[ -x "$SUPERVISOR" ]]; then
  nohup "$SUPERVISOR" >/dev/null 2>&1 &
fi
EOF
  echo "installed autostart hook in $PROFILE"
else
  echo "autostart hook already present"
fi

# External watchdog: independent from FastAPI/uvicorn. If the application or its
# internal watchdog dies, cron makes sure the supervisor itself comes back.
if command -v crontab >/dev/null 2>&1; then
  CRON_MARKER="# litigation-agent-supervisor-watchdog"
  current_cron="$(crontab -l 2>/dev/null || true)"
  if ! grep -qF "$CRON_MARKER" <<<"$current_cron"; then
    {
      printf '%s\n' "$current_cron"
      printf '%s\n' "$CRON_MARKER"
      printf '@reboot nohup %q >/dev/null 2>&1 &\n' "$SUPERVISOR"
      printf '* * * * * pgrep -f %q >/dev/null 2>&1 || nohup %q >/dev/null 2>&1 &\n' "$SUPERVISOR" "$SUPERVISOR"
    } | crontab -
    echo "installed external cron supervisor watchdog"
  else
    echo "external cron supervisor watchdog already present"
  fi
else
  echo "WARNING: crontab unavailable; profile autostart remains as fallback" >&2
fi

"$ROOT/deploy/configure-git-auth.sh" >/dev/null 2>&1 || true
nohup "$SUPERVISOR" >/dev/null 2>&1 &
echo "supervisor ensured running"
