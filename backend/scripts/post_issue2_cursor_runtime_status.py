#!/usr/bin/env python3
"""Post Cursor AgentRuntime P0 status to GitHub Issue #2."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def _run_acceptance(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PYTHON), str(ROOT / "backend" / "scripts" / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output[-1500:] if len(output) > 1500 else output


def main() -> int:
    auth = resolve_github_auth()
    if auth.mode == "none":
        print("SKIP: no GitHub token")
        return 1

    settings = get_autonomous_settings()
    py_version = subprocess.run(
        [str(VENV_PYTHON), "-c", "import sys; print(sys.version.split()[0])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    try:
        import cursor_sdk  # noqa: F401

        import_result = "OK"
    except ImportError as exc:
        import_result = f"FAIL: {exc}"

    runtime_rc, runtime_out = _run_acceptance("live_cursor_agent_runtime_acceptance.py")
    worker_rc, worker_out = _run_acceptance("live_cursor_worker_controlled_acceptance.py")

    test_proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "pytest", "backend/tests/unit/test_autonomous_dev.py", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tests_ok = test_proc.returncode == 0

    p0_seal = (
        runtime_rc == 0
        and worker_rc == 0
        and tests_ok
        and import_result == "OK"
        and settings.cursor_model.strip() != ""
    )

    body = f"""## Cursor AgentRuntime P0 Fix & Acceptance

### Root cause
`autonomous_dev/worker.py` created `AgentOptions` without `model`, causing `ConfigurationError: missing_model: Agent.create requires model` in real `cursor_sdk` runs.

### Modified files
- `autonomous_dev/config.py` — added `cursor_model` + `validate_cursor_sdk_config()`
- `autonomous_dev/worker.py` — wire `settings.cursor_model`; controlled acceptance path
- `.env.example` — `CURSOR_MODEL`
- `deploy/supervise-uvicorn.sh`, `deploy/configure-git-auth.sh`, `deploy/bootstrap-github-deploy-key.sh`, `deploy/git-credential-github.sh` — require project `.venv`
- `backend/tests/unit/test_autonomous_dev.py` — cursor_sdk config/model tests
- `backend/scripts/live_cursor_agent_runtime_acceptance.py`
- `backend/scripts/live_cursor_worker_controlled_acceptance.py`
- `docs/AUTONOMOUS_DEV_RUNBOOK.md`

### Configuration
- **CURSOR_MODEL final value**: `{settings.cursor_model}`
- **AUTONOMOUS_WORKER_MODE**: `{settings.autonomous_worker_mode}`

### Runtime
- **Python**: `{VENV_PYTHON}` ({py_version})
- **cursor_sdk import**: {import_result}

### Real Agent runtime acceptance
```
{runtime_out}
```
**Result**: {"PASS" if runtime_rc == 0 else "FAIL"}

### Worker controlled acceptance
```
{worker_out}
```
**Result**: {"PASS" if worker_rc == 0 else "FAIL"}

### Tests
```
{test_proc.stdout.strip()[-800:]}
```
**Result**: {"PASS" if tests_ok else "FAIL"}

### Remaining blockers
- Issue #2 remains **open** until owner confirms full P0 seal
- Full autonomous loop (webhook → live legal task) not enabled in this change

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
        resp = client.post(url, headers=headers, json={"body": body})
        resp.raise_for_status()
    print("posted Issue #2 cursor runtime status")
    return 0 if p0_seal else 1


if __name__ == "__main__":
    raise SystemExit(main())
