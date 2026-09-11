#!/usr/bin/env python3
"""Post P0 final acceptance report to Issue #2 and seal if all blocking items PASS."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402
from autonomous_dev.github_client import GitHubClient, LABEL_COMPLETED  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def main() -> int:
    auth = resolve_github_auth()
    if auth.mode == "none":
        print("SKIP: no GitHub token")
        return 1

    settings = get_autonomous_settings()
    github = GitHubClient(settings)

    live = subprocess.run(
        [str(VENV_PYTHON), str(ROOT / "backend/scripts/live_p0_production_acceptance.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    live_out = (live.stdout + live.stderr).strip()[-4000:]

    tests = subprocess.run(
        [str(VENV_PYTHON), "-m", "pytest", "backend/tests/unit/test_autonomous_dev.py", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    wt = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    tree_clean = wt.stdout.strip() == ""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    p0_seal = live.returncode == 0 and tests.returncode == 0 and tree_clean

    body = f"""## P0 Final Acceptance Report

**HEAD**: `{head}`

### A–M Live Acceptance
```
{live_out}
```

### Unit tests
```
{tests.stdout.strip()[-500:]}
```
**Result**: {"PASS" if tests.returncode == 0 else "FAIL"}

### Summary
| Check | Result |
|-------|--------|
| exactly-once | see H in live output |
| lease recovery | see lease_recovery |
| label lifecycle | see label_lifecycle |
| product-decision | see product_decision |
| reviewer completion | see reviewer_completion |
| watchdog | see watchdog |
| Sealos deployment | see sealos_deployment |
| working tree clean | {"PASS" if tree_clean else "FAIL"} |

### Remaining blockers
{"None — all blocking items PASS" if p0_seal else "See failed items above"}

### P0 seal
**{"YES" if p0_seal else "NO"}**
"""

    headers = {
        "Authorization": f"Bearer {auth.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{settings.github_repo}/issues/2/comments"
    with httpx.Client(timeout=60.0) as client:
        client.post(url, headers=headers, json={"body": body}).raise_for_status()

        if p0_seal:
            github.sync_completed(2)
            client.patch(
                f"https://api.github.com/repos/{settings.github_repo}/issues/2",
                headers=headers,
                json={"state": "closed", "state_reason": "completed"},
            ).raise_for_status()
            labels = github.get_issue_labels(2)
            if LABEL_COMPLETED not in labels:
                raise RuntimeError("Issue #2 seal label verification failed")

    print(f"posted Issue #2 final report; P0 seal={'YES' if p0_seal else 'NO'}")
    return 0 if p0_seal else 1


if __name__ == "__main__":
    raise SystemExit(main())
