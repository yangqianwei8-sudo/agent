#!/usr/bin/env python3
"""Post GitHub auth remediation status to Issue #2 (requires configured token)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402


def main() -> int:
    auth = resolve_github_auth()
    body = f"""## GitHub Auth Remediation (Issue #2 blocking item)

### Verdict
Non-interactive GitHub auth infrastructure **deployed**. Device Flow / `gh auth login` **removed** from worker path.

### Auth mode detected
- **mode**: `{auth.mode}`
- **interactive**: no (GIT_TERMINAL_PROMPT=0, credential helper / App refresh / SSH)

### Supported methods (priority)
1. **GitHub App** — `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY` (auto-refresh ~1h)
2. **Fine-grained PAT** — `GITHUB_TOKEN` via Sealos Secret `lawyer-agent-secrets` or DevBox env
3. **SSH deploy key** — `GITHUB_SSH_KEY_PATH` + `deploy/bootstrap-github-deploy-key.sh`

### Sealos Secret names
`GITHUB_TOKEN`, `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_SSH_KEY_PATH`, `GITHUB_AUTH_MODE`

### DevBox setup
```bash
./deploy/configure-git-auth.sh
./deploy/verify-git-push.sh
```

### Notes
- Do **not** leave `GITHUB_TOKEN=` empty in `.env` (overwrites injected secrets). Use Sealos/DevBox env injection only.
- Never commit tokens; `.env` remains untracked.
"""
    token = auth.token
    if auth.mode == "none":
        print("SKIP: no GitHub token — comment not posted")
        print(body)
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = "https://api.github.com/repos/yangqianwei8-sudo/agent/issues/2/comments"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers, json={"body": body})
        resp.raise_for_status()
    print("posted Issue #2 auth status comment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
