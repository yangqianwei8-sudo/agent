#!/usr/bin/env bash
# Non-interactive git credential helper — PAT or GitHub App installation token only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "${1:-}" != "get" ]]; then
  exit 0
fi

mapfile -t lines
host=""
protocol=""
for line in "${lines[@]}"; do
  key="${line%%=*}"
  val="${line#*=}"
  case "$key" in
    host) host="$val" ;;
    protocol) protocol="$val" ;;
  esac
done

if [[ "$host" != "github.com" || "$protocol" != "https" ]]; then
  exit 1
fi

token="$(
  python3 - <<'PY'
from autonomous_dev.github_auth import resolve_github_token
print(resolve_github_token())
PY
)"

if [[ -z "$token" ]]; then
  exit 1
fi

printf 'username=x-access-token\n'
printf 'password=%s\n' "$token"
