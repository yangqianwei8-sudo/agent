# Autonomous Dev Runbook (Sealos / DevBox)

## Architecture

```text
GitHub Issue (cursor-task + current-task)
  → POST /webhooks/github
  → TaskRouter → Worker (one at a time)
  → tests → commit → push main
  → push webhook → ready-for-review
  → event-driven OpenAI reviewer bridge
  → PASS/FAIL/PRODUCT_DECISION → GitHub state transition
```

Hourly watchdog scans stale `ready-for-review` tasks and orphaned `current-task` issues as fallback only.

## State machine (GitHub labels = external SSOT)

```text
current-task → worker-running → ready-for-review → completed
failures: worker-running → needs-fix
product ambiguity: worker-running → product-decision
```

- GitHub labels are updated via `GitHubClient` (fail-closed — missing token or API failure raises error)
- SQLite (`StateStore`) holds local execution/delivery state
- `completed` via event-driven reviewer PASS (OpenAI API) or manual `POST /autonomous/review/pass`, never by Worker

## Exactly-once execution

In addition to `X-GitHub-Delivery` dedup, task execution identity is:

```text
{GITHUB_REPO}#{issue_number}#{issue.updated_at}
```

Same active execution (queued/running/ready-for-review) blocks duplicate worker starts even with a new delivery ID.

## Lease lock

Worker lease persisted in SQLite: `owner`, `acquired_at`, `heartbeat_at`, `lease_expires_at`.

- Heartbeat extends lease during long runs
- Stale lease auto-recovered on next webhook or watchdog tick
- Only one worker holds lease at a time

## Python runtime (required)

All autonomous dev services **must** use the project virtualenv — **not** system `python3.10`.

```bash
/home/devbox/project/.venv/bin/python --version   # expect 3.11+
```

Deploy scripts (`deploy/supervise-uvicorn.sh`, `deploy/configure-git-auth.sh`, etc.) fail fast if `.venv` is missing. Uvicorn is started via `.venv/bin/uvicorn`.

Create venv if needed:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Required env vars

| Variable | Description |
|----------|-------------|
| `AUTONOMOUS_DEV_ENABLED` | `true` to enable webhook routes |
| `GITHUB_WEBHOOK_SECRET` | GitHub webhook HMAC secret |
| `GITHUB_TOKEN` | Fine-grained PAT (Option A — Sealos Secret / DevBox env) |
| `GITHUB_APP_ID` / `GITHUB_APP_INSTALLATION_ID` / `GITHUB_APP_PRIVATE_KEY` | GitHub App (Option B — auto-refreshed installation token) |
| `GITHUB_SSH_KEY_PATH` | SSH deploy key path (Option C) |
| `GITHUB_AUTH_MODE` | `auto` (default), `pat`, `app`, or `ssh` |
| `CURSOR_API_KEY` | Cursor API key for `cursor_sdk` worker mode |
| `CURSOR_MODEL` | Cursor agent model (default `composer-2`) |
| `AUTONOMOUS_WORKER_MODE` | `deterministic` (tests), `live`, or `cursor_sdk` |
| `AUTONOMOUS_REPO_ROOT` | Repository root path |
| `AUTONOMOUS_STATE_DB_PATH` | SQLite state DB (default `data/autonomous_dev.db`) |
| `WORKER_LEASE_TTL_SECONDS` | Worker lease TTL (default 300) |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | Lease heartbeat interval (default 30) |
| `WATCHDOG_INTERVAL_SECONDS` | Hourly fallback scan interval (default 3600) |
| `REVIEW_WATCHDOG_STALE_SECONDS` | Re-trigger review handoff after this age (default 3600) |
| `OPENAI_API_KEY` | Independent reviewer API key (reviewer-only; does **not** fall back to `LLM_API_KEY`) |
| `REVIEWER_MODEL` | Reviewer model (default `gpt-4o-mini`) |
| `REVIEWER_BASE_URL` | Reviewer API base URL (default `https://api.openai.com/v1`) |
| `REVIEWER_LEASE_TTL_SECONDS` | Reviewer lock TTL (default 300) |

Never commit `.env`. **Do not** leave `GITHUB_TOKEN=` or `CURSOR_API_KEY=` empty in `.env` — empty values overwrite secrets injected by Sealos/DevBox.

### cursor_sdk mode validation (fail closed)

When `AUTONOMOUS_WORKER_MODE=cursor_sdk`:

- `CURSOR_API_KEY` must be set — otherwise worker fails with explicit error
- `CURSOR_MODEL` must be set — otherwise worker fails with explicit error
- No silent fallback to `deterministic`

Secrets are never logged or included in error messages.

## GitHub auth (non-interactive — no `gh auth` / device flow)

Autonomous Worker **must not** use GitHub Device Flow, browser login, or `gh auth login`.

Supported methods (priority):

1. **GitHub App installation token** (production) — `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY` or `_PATH`. Tokens refresh automatically (~1h TTL).
2. **Fine-grained PAT** — inject `GITHUB_TOKEN` via Sealos Secret (`lawyer-agent-secrets`) or DevBox Advanced Config env.
3. **SSH deploy key** — set `GITHUB_SSH_KEY_PATH`; register once with `deploy/bootstrap-github-deploy-key.sh` (requires PAT/App for API registration).

Setup on DevBox (persists across sessions):

```bash
chmod +x deploy/configure-git-auth.sh deploy/verify-git-push.sh
./deploy/configure-git-auth.sh
GIT_TERMINAL_PROMPT=0 git push origin main   # must succeed without browser
```

Verify:

```bash
./deploy/verify-git-push.sh
```

Sealos Secret names (see `deploy/sealos/secret.example.yaml`): `GITHUB_TOKEN`, `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_SSH_KEY_PATH`, `GITHUB_AUTH_MODE`, `CURSOR_API_KEY`.

## Webhook URL

Production (Sealos DevBox public ingress, container port 8080):

```text
https://ynboesvphjna.sealosbja.site/webhooks/github
```

Set `AUTONOMOUS_WEBHOOK_PUBLIC_URL` to match. Do not use stale hostnames that return 503.

Enable GitHub events: `Issues`, `Push`.

## Health check

```text
GET /healthz
```

## Long-running service (DevBox)

```bash
chmod +x deploy/supervise-uvicorn.sh deploy/install-devbox-autostart.sh
./deploy/install-devbox-autostart.sh
```

Supervisor restarts uvicorn automatically on exit. Logs: `data/uvicorn-supervisor.log`.

Manual restart:

```bash
pkill -f supervise-uvicorn.sh || true
nohup ./deploy/supervise-uvicorn.sh >/dev/null 2>&1 &
```

## Cursor Agent runtime verification (P0)

Minimal live acceptance — uses real `.env`, real Cursor SDK, and `settings.cursor_model` (no hardcoded model in script):

```bash
git status   # note state before
.venv/bin/python backend/scripts/live_cursor_agent_runtime_acceptance.py
git status   # must be unchanged (agent must not modify files)
```

Expected marker in output: `CURSOR_AGENT_RUNTIME_OK`

Worker controlled acceptance (cursor_sdk branch, no legal business execution):

```bash
.venv/bin/python backend/scripts/live_cursor_worker_controlled_acceptance.py
```

Controlled acceptance uses issue body marker `[CURSOR-RUNTIME-ACCEPTANCE]` — safe prompt only, no file modifications.

P0 production live acceptance (full A–M):

```bash
pkill -f "uvicorn backend.main:app" || true
nohup .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
.venv/bin/python backend/scripts/live_p0_production_acceptance.py
```

## Sealos deployment

Manifests in `deploy/sealos/`:

- `deployment.yaml` — Deployment with `.venv/bin/uvicorn`, readiness/liveness on `/healthz`, restartPolicy Always
- `service.yaml` — ClusterIP port 8000
- `ingress.yaml` — `/webhooks/github`, `/healthz`
- `pvc.yaml` — persistent SQLite state at `/app/data`
- `configmap.yaml` — non-secret env (worker mode, repo, lease/watchdog intervals)
- `secret.example.yaml` — template for webhook secret, GitHub token, Cursor API key (never commit real values)

Apply:

```bash
kubectl apply -f deploy/sealos/pvc.yaml
kubectl apply -f deploy/sealos/configmap.yaml
kubectl apply -f deploy/sealos/secret.example.yaml   # replace with real Secret first
kubectl apply -f deploy/sealos/deployment.yaml
kubectl apply -f deploy/sealos/service.yaml
kubectl apply -f deploy/sealos/ingress.yaml
```

## Event-driven reviewer bridge (P0.2)

Primary path: push webhook → `ready-for-review` → `OpenAIApiReviewAdapter` → `ReviewExecutor` → OpenAI API.

- Exactly-once per `(task_id, commit_sha)` in `review_invocations` table
- Reviewer lock prevents concurrent reviews on same task
- Idempotent replay on duplicate push deliveries
- Watchdog fallback re-schedules stale reviews (hourly only)
- Credential blocker: missing `OPENAI_API_KEY` → fail closed; `LLM_API_KEY` (worker) does **not** substitute reviewer

Verdicts: `PASS` (seal/close), `FAIL` (repair issue + `current-task`), `PRODUCT_DECISION` (Chinese packet, halt).

P0.2 live acceptance:

```bash
.venv/bin/python backend/scripts/live_p02_reviewer_acceptance.py
```

## Reviewer PASS (manual seal fallback)

```bash
curl -X POST http://127.0.0.1:8000/autonomous/review/pass \
  -H "X-Autonomous-Secret: $GITHUB_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"issue_number": 2}'
```

Transitions `ready-for-review` → `completed` on GitHub and in DB.

## Failure recovery

- Invalid signature → HTTP 401, no worker started
- Duplicate `X-GitHub-Delivery` → ignored (idempotent)
- Worker crash → lock released in `finally`; re-deliver issue webhook
- Process exit → supervisor restarts uvicorn within 2s
- Missing `CURSOR_API_KEY` / `CURSOR_MODEL` in cursor_sdk mode → `NEEDS_FIX`, no silent fallback

## Labels state machine

```text
cursor-task + current-task → worker-running → ready-for-review → completed
failures → needs-fix
product ambiguity → product-decision (halt)
```

Only one `current-task` worker may run at a time.
