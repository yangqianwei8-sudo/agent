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

## Required env vars

| Variable | Description |
|----------|-------------|
| `AUTONOMOUS_DEV_ENABLED` | `true` to enable webhook routes |
| `GITHUB_WEBHOOK_SECRET` | GitHub webhook HMAC secret |
| `GITHUB_TOKEN` | GitHub API token (live worker push) |
| `AUTONOMOUS_WORKER_MODE` | `deterministic` (tests) or `live` |
| `AUTONOMOUS_REPO_ROOT` | Repository root path |
| `AUTONOMOUS_STATE_DB_PATH` | SQLite state DB (default `data/autonomous_dev.db`) |

Never commit `.env`.

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

## Failure recovery

- Invalid signature → HTTP 401, no worker started
- Duplicate `X-GitHub-Delivery` → ignored (idempotent)
- Worker crash → lock released in `finally`; re-deliver issue webhook
- Process exit → supervisor restarts uvicorn within 2s

## Labels state machine

```text
cursor-task + current-task → worker-running → ready-for-review → completed
failures → needs-fix
product ambiguity → product-decision (halt)
```

Only one `current-task` worker may run at a time.
