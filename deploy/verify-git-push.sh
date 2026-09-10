#!/usr/bin/env bash
# Issue #2 — verify non-interactive git push (no browser / device flow).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

"$ROOT/deploy/configure-git-auth.sh"

export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/false

git fetch origin
git status --short --branch
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"

echo "local HEAD=$LOCAL"
echo "origin/main=$REMOTE"

GIT_TERMINAL_PROMPT=0 git push origin main

LOCAL_AFTER="$(git rev-parse HEAD)"
REMOTE_AFTER="$(git rev-parse origin/main)"

if [[ "$LOCAL_AFTER" != "$REMOTE_AFTER" ]]; then
  echo "FAIL: HEAD != origin/main after push"
  exit 1
fi

echo "PASS: non-interactive push OK, HEAD == origin/main"
