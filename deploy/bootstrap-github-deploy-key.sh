#!/usr/bin/env bash
# One-time: register SSH deploy key when PAT/App token is available, then prefer SSH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/deploy/load-env.sh" "$ROOT/.env"

KEY="${GITHUB_SSH_KEY_PATH:-$HOME/.ssh/github_agent_deploy}"
PUB="${KEY}.pub"

if [[ ! -f "$KEY" ]]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "lawyer-agent-deploy@sealos"
fi
chmod 600 "$KEY"
ssh-keyscan github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true

export GITHUB_SSH_KEY_PATH="$KEY"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 - "$PUB" <<'PY'
import sys
from pathlib import Path

from autonomous_dev.github_auth import register_deploy_key, resolve_github_auth

auth = resolve_github_auth()
if auth.mode not in ("app", "pat"):
    raise SystemExit("Need GITHUB_TOKEN or GitHub App credentials to register deploy key")

register_deploy_key(public_key_path=Path(sys.argv[1]))
print("deploy key registered")
PY

export GITHUB_AUTH_MODE=ssh
"$ROOT/deploy/configure-git-auth.sh"
