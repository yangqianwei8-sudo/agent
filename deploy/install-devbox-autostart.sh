#!/usr/bin/env bash
# Install DevBox autostart hook — runs supervised uvicorn after SSH session starts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# litigation-agent-autostart"
PROFILE="${HOME}/.profile"

if ! grep -q "$MARKER" "$PROFILE" 2>/dev/null; then
  cat >> "$PROFILE" <<EOF

$MARKER
if [[ -x "$ROOT/deploy/supervise-uvicorn.sh" ]]; then
  nohup "$ROOT/deploy/supervise-uvicorn.sh" >/dev/null 2>&1 &
fi
EOF
  echo "installed autostart hook in $PROFILE"
else
  echo "autostart hook already present"
fi

chmod +x "$ROOT/deploy/supervise-uvicorn.sh"
nohup "$ROOT/deploy/supervise-uvicorn.sh" >/dev/null 2>&1 &
echo "supervisor started"
