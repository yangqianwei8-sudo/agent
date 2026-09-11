#!/usr/bin/env python3
"""Post P0.2 repair evidence summary to GitHub Issue #20."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402
from autonomous_dev.github_client import GitHubClient  # noqa: E402

EVIDENCE_DIR = ROOT / "data" / "p02_repair_evidence"


def _latest_evidence() -> tuple[Path, Path]:
    files = sorted(EVIDENCE_DIR.glob("repair_evidence_*.json"))
    if not files:
        raise SystemExit("No repair evidence found — run generate_p02_repair_evidence.py first")
    latest = files[-1]
    txt = latest.with_suffix(".txt")
    return latest, txt


def main() -> int:
    auth = resolve_github_auth()
    if auth.mode == "none":
        print("SKIP: no GitHub token")
        return 1

    json_path, txt_path = _latest_evidence()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    origin = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()

    summary = txt_path.read_text(encoding="utf-8")
    if len(summary) > 60000:
        summary = summary[:60000] + "\n\n... [truncated for GitHub comment limit; full artifact on runner]"

    body = f"""## P0.2 Repair Report (Issue #20 → #15)

**Commit**: `{head}`
**HEAD == origin/main**: `{head == origin}` 
**Working tree clean**: `{porcelain == ""}`

### Reviewer independence fix
Removed `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` fallback from `resolve_reviewer_credentials()`.
Reviewer uses **only** `OPENAI_API_KEY` + `REVIEWER_*` — worker LLM (DeepSeek) cannot substitute.

### Full evidence artifacts
```
{summary}
```

Evidence files on runner:
- `{json_path.relative_to(ROOT)}`
- `{txt_path.relative_to(ROOT)}`
"""

    settings = get_autonomous_settings()
    github = GitHubClient(settings)
    github.add_comment(20, body)
    print(f"Posted repair report to Issue #20 (commit {head})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
