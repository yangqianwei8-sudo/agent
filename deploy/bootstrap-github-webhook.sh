#!/usr/bin/env bash
# Register or update the production GitHub repository webhook (requires Webhooks write).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/deploy/load-env.sh" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/deploy/load-env.sh" "$ROOT/.env"
fi

PUBLIC_URL="${AUTONOMOUS_WEBHOOK_PUBLIC_URL:-https://ynboesvphjna.sealosbja.site}"
WEBHOOK_URL="${PUBLIC_URL%/}/webhooks/github"
SECRET="${GITHUB_WEBHOOK_SECRET:-}"
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
export WEBHOOK_URL SECRET

if [[ -z "$SECRET" ]]; then
  echo "ERROR: GITHUB_WEBHOOK_SECRET not set" >&2
  exit 1
fi
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: GITHUB_TOKEN not set" >&2
  exit 1
fi

if [[ "${SKIP_PUBLIC_HEALTH_CHECK:-}" != "1" ]]; then
  echo "Checking public endpoint: ${PUBLIC_URL}/healthz"
  health_code="$(curl -sS -m 10 -o /tmp/webhook-health.json -w '%{http_code}' "${PUBLIC_URL}/healthz" || echo 000)"
  if [[ "$health_code" != "200" ]]; then
    echo "ERROR: public /healthz returned HTTP ${health_code} (expected 200)" >&2
    echo "Ensure uvicorn binds 0.0.0.0:${APP_PORT:-8000} and DevBox port public access is enabled." >&2
    echo "If checking from inside the cluster (hairpin NAT), retry with SKIP_PUBLIC_HEALTH_CHECK=1." >&2
    exit 1
  fi
else
  echo "SKIP_PUBLIC_HEALTH_CHECK=1 — skipping external /healthz probe"
fi

payload="$(python3 - <<PY
import json, os
print(json.dumps({
    "name": "web",
    "active": True,
    "events": ["issues", "push"],
    "config": {
        "url": os.environ["WEBHOOK_URL"],
        "content_type": "json",
        "secret": os.environ["SECRET"],
        "insecure_ssl": "0",
    },
}))
PY
)"

list_resp="$(curl -sS -w '\n%{http_code}' \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/yangqianwei8-sudo/agent/hooks")"
list_body="${list_resp%$'\n'*}"
list_code="${list_resp##*$'\n'}"

if [[ "$list_code" == "403" ]]; then
  echo "ERROR: token lacks Webhooks permission (HTTP 403). Grant Webhooks read/write on the PAT or run .github/workflows/bootstrap-autonomous-webhook.yml" >&2
  exit 1
fi
if [[ "$list_code" != "200" ]]; then
  echo "ERROR: list hooks failed HTTP ${list_code}: ${list_body}" >&2
  exit 1
fi

hook_id="$(python3 - <<PY
import json, os, sys
hooks = json.loads("""${list_body//\"/\\\"}""")
target = os.environ["WEBHOOK_URL"]
for h in hooks:
    if h.get("config", {}).get("url") == target:
        print(h["id"])
        break
else:
    for h in hooks:
        if "webhooks/github" in (h.get("config", {}).get("url") or ""):
            print(h["id"])
            break
PY
)"

if [[ -n "$hook_id" ]]; then
  echo "Updating existing webhook id=${hook_id} -> ${WEBHOOK_URL}"
  resp="$(curl -sS -w '\n%{http_code}' -X PATCH \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/yangqianwei8-sudo/agent/hooks/${hook_id}" \
    -d "$payload")"
else
  echo "Creating webhook -> ${WEBHOOK_URL}"
  resp="$(curl -sS -w '\n%{http_code}' -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/yangqianwei8-sudo/agent/hooks" \
    -d "$payload")"
fi

body="${resp%$'\n'*}"
code="${resp##*$'\n'}"
if [[ "$code" != "200" && "$code" != "201" ]]; then
  echo "ERROR: webhook upsert failed HTTP ${code}: ${body}" >&2
  exit 1
fi

hook_id="$(python3 - <<PY
import json
print(json.loads("""${body//\"/\\\"}""")["id"])
PY
)"

ping_resp="$(curl -sS -w '\n%{http_code}' -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/yangqianwei8-sudo/agent/hooks/${hook_id}/pings")"
ping_code="${ping_resp##*$'\n'}"
echo "Webhook id=${hook_id} ping HTTP ${ping_code}"
if [[ "$ping_code" != "204" ]]; then
  echo "WARN: ping did not return 204" >&2
fi

echo "OK: webhook configured at ${WEBHOOK_URL}"
