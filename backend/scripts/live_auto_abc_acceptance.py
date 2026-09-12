#!/usr/bin/env python3
"""Live AUTO-A → AUTO-B → AUTO-C unmanned acceptance (single manual activation of A)."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_client import GitHubClient  # noqa: E402
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "https://ynboesvphjna.sealosbja.site")
POLL_SECONDS = int(os.environ.get("AUTO_ABC_POLL_SECONDS", "600"))
DURABILITY_SECONDS = int(os.environ.get("AUTO_ABC_DURABILITY_SECONDS", "1800"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _create_chain(github: GitHubClient) -> tuple[int, int, int]:
    suffix = uuid.uuid4().hex[:8]
    issue_c = github.create_issue(
        title=f"[AUTO-C] Unmanned acceptance chain {suffix}",
        body=(
            f"{REVIEWER_ACCEPTANCE_MARKER}\n"
            "[P0-LIVE-ACCEPTANCE]\n"
            "Harmless marker-only change for AUTO-C.\n"
            "Update autonomous_dev/acceptance_marker.txt only."
        ),
        labels=["cursor-task"],
    )
    issue_b = github.create_issue(
        title=f"[AUTO-B] Unmanned acceptance chain {suffix}",
        body=(
            f"{REVIEWER_ACCEPTANCE_MARKER}\n"
            "[P0-LIVE-ACCEPTANCE]\n"
            f"Harmless marker-only change for AUTO-B.\n"
            f"NEXT_TASK: [AUTO-C] Unmanned acceptance chain {suffix}\n"
            "Update autonomous_dev/acceptance_marker.txt only."
        ),
        labels=["cursor-task"],
    )
    issue_a = github.create_issue(
        title=f"[AUTO-A] Unmanned acceptance chain {suffix}",
        body=(
            f"{REVIEWER_ACCEPTANCE_MARKER}\n"
            "[P0-LIVE-ACCEPTANCE]\n"
            f"Harmless marker-only change for AUTO-A.\n"
            f"NEXT_TASK: [AUTO-B] Unmanned acceptance chain {suffix}\n"
            "Update autonomous_dev/acceptance_marker.txt only."
        ),
        labels=["cursor-task"],
    )
    return issue_a, issue_b, issue_c


def _activate_a(github: GitHubClient, issue_a: int) -> None:
    github.enforce_single_current_task(issue_a)


def _wait_task_state(
    store: StateStore,
    issue: int,
    *,
    want: set[TaskStatus],
    timeout: int,
) -> tuple[bool, dict | None]:
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        task = store.get_task_by_issue(issue)
        if task:
            last = {
                "id": task.id,
                "status": task.status.value,
                "commit_sha": task.commit_sha,
                "error": task.error,
            }
            if task.status in want:
                return True, last
        time.sleep(2)
    return False, last


def _fetch_status(client: httpx.Client) -> dict:
    resp = client.get(f"{BASE}/autonomous/status.json", timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _record_step(name: str, data: dict) -> None:
    out = ROOT / "data" / "auto_abc_acceptance.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now(), "step": name, **data}, ensure_ascii=False) + "\n")


def run_acceptance() -> int:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    github = GitHubClient(settings)
    results: dict[str, str] = {}
    timings: dict[str, float] = {}

    issue_a, issue_b, issue_c = _create_chain(github)
    _record_step("created", {"issue_a": issue_a, "issue_b": issue_b, "issue_c": issue_c})
    print(f"Created AUTO-A=#{issue_a} AUTO-B=#{issue_b} AUTO-C=#{issue_c}")

    _activate_a(github, issue_a)
    _record_step("activated_a", {"issue_a": issue_a, "manual_interventions": 0, "started_at": _now()})
    print(f"Activated AUTO-A #{issue_a} (programmatic — no human steps)")

    chain = [
        ("AUTO-A", issue_a, issue_b),
        ("AUTO-B", issue_b, issue_c),
        ("AUTO-C", issue_c, None),
    ]

    for label, issue, next_issue in chain:
        ok, info = _wait_task_state(
            store,
            issue,
            want={TaskStatus.READY_FOR_REVIEW},
            timeout=POLL_SECONDS,
        )
        results[f"{label}_ready"] = "PASS" if ok else "FAIL"
        if info:
            _record_step(f"{label}_ready", info)

        ok, info = _wait_task_state(
            store,
            issue,
            want={TaskStatus.COMPLETED},
            timeout=POLL_SECONDS,
        )
        results[f"{label}_sealed"] = "PASS" if ok else "FAIL"
        if info:
            _record_step(f"{label}_sealed", info)

        if next_issue is not None:
            delay_start = time.monotonic()
            ok, info = _wait_task_state(
                store,
                next_issue,
                want={TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.READY_FOR_REVIEW},
                timeout=25,
            )
            delay = round(time.monotonic() - delay_start, 2)
            timings[f"{label}_to_next_s"] = delay
            results[f"{label}_handoff_20s"] = "PASS" if ok and delay <= 20 else "FAIL"
            _record_step(f"{label}_handoff", {"next_issue": next_issue, "delay_s": delay, **(info or {})})

    with httpx.Client() as client:
        print(f"Durability watch {DURABILITY_SECONDS}s …")
        t_dur = time.monotonic()
        stale_hits = 0
        while time.monotonic() - t_dur < DURABILITY_SECONDS:
            status = _fetch_status(client)
            dep = status.get("deployment") or {}
            sys_status = status.get("system_status")
            if dep.get("status") == "STALE":
                stale_hits += 1
            if sys_status in {"STALE", "FAILED"}:
                stale_hits += 1
            time.sleep(30)
        results["durability_30m"] = "PASS" if stale_hits == 0 else "FAIL"
        timings["durability_s"] = round(time.monotonic() - t_dur, 1)

        hz = client.get(f"{BASE}/healthz", timeout=15.0).json()
        main_sha = dep.get("main_sha") or ""
        runtime_sha = (status.get("runtime_version") or {}).get("git_sha") or hz.get("git_sha") or ""
        results["runtime_equality"] = (
            "PASS"
            if main_sha and runtime_sha and main_sha[:12] == runtime_sha[:12]
            else "FAIL"
        )
        _record_step(
            "runtime",
            {"main_sha": main_sha, "runtime_sha": runtime_sha, "healthz_sha": hz.get("git_sha")},
        )

    print("=== AUTO-A/B/C Live Acceptance ===")
    for k, v in results.items():
        print(f"{k}: {v}")
    for k, v in timings.items():
        print(f"{k}: {v}")
    failed = [k for k, v in results.items() if v == "FAIL"]
    if failed:
        print(f"Final: FAIL ({', '.join(failed)})")
        return 1
    print("Final: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_acceptance())
