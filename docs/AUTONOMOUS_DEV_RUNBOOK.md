# Autonomous Dev Runbook (Sealos / DevBox)

## Architecture

```text
GitHub Issue (cursor-task + current-task)
  → POST /webhooks/github
  → TaskRouter → Worker (one at a time)
  → tests → commit → push main
  → push webhook → ready-for-review
```

Hourly ChatGPT watchdog remains fallback via `WatchdogFallbackAdapter`.

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

```text
https://<your-devbox-or-sealos-host>/webhooks/github
```

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
