#!/usr/bin/env bash
# Source .env without overwriting secrets already injected by Sealos/K8s.
set -euo pipefail

ENV_FILE="${1:-.env}"
[[ -f "$ENV_FILE" ]] || return 0

PRESERVE_KEYS=(
  GITHUB_TOKEN GH_TOKEN GITHUB_PAT
  GITHUB_APP_ID GITHUB_APP_INSTALLATION_ID
  GITHUB_APP_PRIVATE_KEY GITHUB_APP_PRIVATE_KEY_PATH
  GITHUB_SSH_KEY_PATH GITHUB_AUTH_MODE
  CURSOR_API_KEY
)

# Sealos/DevBox injects APP_PORT for public ingress — never let .env override it.
FORCE_PRESERVE_KEYS=(APP_PORT APP_HOST)

declare -A preserved=()
for key in "${PRESERVE_KEYS[@]}" "${FORCE_PRESERVE_KEYS[@]}"; do
  if [[ -n "${!key:-}" ]]; then
    preserved["$key"]="${!key}"
  fi
done

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for key in "${!preserved[@]}"; do
  if [[ " ${FORCE_PRESERVE_KEYS[*]} " == *" ${key} "* ]] || [[ -z "${!key:-}" ]]; then
    export "$key=${preserved[$key]}"
  fi
done

# DevBox runtime version — align healthz/status with git HEAD when not injected by K8s.
if [[ -z "${GIT_SHA:-}" ]] && command -v git >/dev/null 2>&1; then
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    export GIT_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
    export IMAGE_TAG="${IMAGE_TAG:-$GIT_SHA}"
  fi
fi

# DevBox public ingress port — must match Sealos Network container port (see .sealos/preview.yaml).
if [[ -n "${DEVBOX_JWT_SECRET:-}" ]]; then
  export APP_PORT=8080
fi
