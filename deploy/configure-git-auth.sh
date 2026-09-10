#!/usr/bin/env bash
# Configure non-interactive GitHub auth for worker + shell (no gh device flow).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

while IFS= read -r line; do
  key="${line%%=*}"
  val="${line#*=}"
  if [[ -n "$key" && -z "${!key:-}" && -n "$val" ]]; then
    export "$key=$val"
  fi
done < <(
  python3 - <<'PY'
import os
from autonomous_dev.config import get_autonomous_settings

s = get_autonomous_settings()
mapping = {
    "GITHUB_TOKEN": s.github_token,
    "GITHUB_AUTH_MODE": s.github_auth_mode,
    "GITHUB_APP_ID": s.github_app_id,
    "GITHUB_APP_INSTALLATION_ID": s.github_app_installation_id,
    "GITHUB_APP_PRIVATE_KEY": s.github_app_private_key,
    "GITHUB_APP_PRIVATE_KEY_PATH": s.github_app_private_key_path,
    "GITHUB_SSH_KEY_PATH": s.github_ssh_key_path,
    "GITHUB_TOKEN_FILE": s.github_token_file,
}
for key, value in mapping.items():
    if value and not os.environ.get(key):
        print(f"{key}={value}")
PY
)

export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/false
unset SSH_ASKPASS GH_TOKEN 2>/dev/null || true
for _empty_key in GITHUB_TOKEN GH_TOKEN GITHUB_PAT; do
  if [[ -z "${!_empty_key:-}" ]]; then
    unset "$_empty_key" 2>/dev/null || true
  fi
done

HELPER="$ROOT/deploy/git-credential-github.sh"
chmod +x "$HELPER" "$ROOT/deploy/load-env.sh" 2>/dev/null || true

# Local repo config — never store token in config files.
git config --local credential.helper ""
git config --local --add credential.helper "$HELPER"
git config --local credential.useHttpPath true
git config --local core.askPass /bin/false

# Disable gh as credential source if present.
git config --global --unset-all credential.https://github.com.helper 2>/dev/null || true

AUTH_MODE="$(
  python3 - <<'PY'
from autonomous_dev.github_auth import resolve_github_auth
auth = resolve_github_auth()
print(auth.mode)
PY
)"

case "$AUTH_MODE" in
  ssh)
    KEY="$(
      python3 - <<'PY'
from autonomous_dev.github_auth import resolve_github_auth
auth = resolve_github_auth()
print(auth.ssh_key_path or "")
PY
    )"
    if [[ -n "$KEY" ]]; then
      git config --local url."git@github.com:".insteadOf "https://github.com/"
      export GIT_SSH_COMMAND="ssh -i ${KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
    fi
    ;;
  app|pat)
    git config --local --unset-all url.git@github.com:.insteadof 2>/dev/null || true
    ;;
esac

echo "git auth configured mode=${AUTH_MODE} (non-interactive, no device flow)"
