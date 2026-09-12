#!/usr/bin/env python3
"""Audit open GitHub issues against Autonomous Dev persisted state — per-issue reconcile."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_auth import resolve_github_token  # noqa: E402
from autonomous_dev.github_client import (  # noqa: E402
    LABEL_COMPLETED,
    LABEL_CURRENT_TASK,
    LABEL_CURSOR_TASK,
    LABEL_NEEDS_FIX,
    LABEL_PRODUCT_DECISION,
    LABEL_READY_FOR_REVIEW,
    LABEL_WORKER_RUNNING,
    GitHubClient,
)
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402

RUNTIME_LABELS = frozenset(
    {
        LABEL_CURRENT_TASK,
        LABEL_WORKER_RUNNING,
        LABEL_READY_FOR_REVIEW,
        LABEL_NEEDS_FIX,
        LABEL_PRODUCT_DECISION,
        LABEL_COMPLETED,
    }
)


@dataclass
class IssueDecision:
    issue_number: int
    title: str
    github_labels: set[str]
    task_status: str | None
    task_commit: str | None
    review_verdict: str | None
    action: str
    detail: str


def _list_open_issues(repo: str, token: str) -> list[dict]:
    issues: list[dict] = []
    page = 1
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=60.0) as client:
        while True:
            resp = client.get(
                f"https://api.github.com/repos/{repo}/issues",
                headers=headers,
                params={"state": "open", "per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                if "pull_request" in item:
                    continue
                issues.append(item)
            if len(batch) < 100:
                break
            page += 1
    return issues


def decide_issue(
    *,
    issue_number: int,
    title: str,
    github_labels: set[str],
    store: StateStore,
) -> IssueDecision:
    task = store.get_task_by_issue(issue_number)
    task_status = task.status.value if task else None
    task_commit = task.commit_sha if task else None

    review_verdict = None
    with store._conn() as conn:  # noqa: SLF001
        row = conn.execute(
            """
            SELECT verdict FROM review_invocations
            WHERE issue_number = ? ORDER BY created_at DESC LIMIT 1
            """,
            (issue_number,),
        ).fetchone()
        if row and row["verdict"]:
            review_verdict = str(row["verdict"])

    if task and task.status == TaskStatus.COMPLETED:
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "close",
            f"task completed sealed commit={task_commit[:12] if task_commit else 'none'}",
        )

    if task and task.status == TaskStatus.PRODUCT_DECISION:
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_product_decision",
            "awaiting product direction",
        )

    if task and task.status == TaskStatus.NEEDS_FIX:
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_needs_fix",
            f"reviewer FAIL or worker needs-fix commit={task_commit[:12] if task_commit else 'none'}",
        )

    if task and task.status == TaskStatus.READY_FOR_REVIEW:
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_ready_for_review",
            f"awaiting reviewer commit={task_commit[:12] if task_commit else 'none'}",
        )

    if task and task.status == TaskStatus.RUNNING:
        lease = store.get_lease()
        now_iso = datetime.now(UTC).isoformat()
        valid = (
            lease.locked
            and lease.issue_number == issue_number
            and lease.task_id == task.id
            and store._lease_is_valid(lease, now_iso=now_iso)
        )
        if valid:
            return IssueDecision(
                issue_number,
                title,
                github_labels,
                task_status,
                task_commit,
                review_verdict,
                "sync_worker_running",
                f"active worker lease owner={lease.owner}",
            )
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_cursor_task_only",
            "running in DB without valid lease — strip stale runtime labels",
        )

    if task and task.status == TaskStatus.FAILED:
        if review_verdict == "FAIL":
            return IssueDecision(
                issue_number,
                title,
                github_labels,
                task_status,
                task_commit,
                review_verdict,
                "sync_needs_fix",
                f"failed after review FAIL: {task.error or ''}"[:120],
            )
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_cursor_task_only",
            f"failed execution: {(task.error or '')[:120]}",
        )

    stale = github_labels & RUNTIME_LABELS
    if stale:
        return IssueDecision(
            issue_number,
            title,
            github_labels,
            task_status,
            task_commit,
            review_verdict,
            "sync_cursor_task_only",
            f"no task record; remove stale labels {sorted(stale)}",
        )

    return IssueDecision(
        issue_number,
        title,
        github_labels,
        task_status,
        task_commit,
        review_verdict,
        "keep",
        "queued/open with no conflicting runtime labels",
    )


def expected_labels(decision: IssueDecision) -> set[str]:
    if decision.action == "close":
        return {LABEL_CURSOR_TASK, LABEL_COMPLETED}
    if decision.action == "sync_needs_fix":
        return {LABEL_CURSOR_TASK, LABEL_NEEDS_FIX}
    if decision.action == "sync_product_decision":
        return {LABEL_CURSOR_TASK, LABEL_PRODUCT_DECISION}
    if decision.action == "sync_ready_for_review":
        return {LABEL_CURSOR_TASK, LABEL_READY_FOR_REVIEW}
    if decision.action == "sync_worker_running":
        return {LABEL_CURSOR_TASK, LABEL_WORKER_RUNNING}
    if decision.action == "sync_cursor_task_only":
        return {LABEL_CURSOR_TASK}
    return decision.github_labels


def labels_aligned(decision: IssueDecision) -> bool:
    if decision.action == "keep":
        stale = decision.github_labels & RUNTIME_LABELS
        return not stale
    runtime = decision.github_labels & (RUNTIME_LABELS | {LABEL_CURSOR_TASK})
    return runtime == expected_labels(decision)


def apply_decision(github: GitHubClient, decision: IssueDecision, *, dry_run: bool) -> dict:
    n = decision.issue_number
    result = {"issue": n, "action": decision.action, "detail": decision.detail, "applied": False}

    if decision.action == "keep":
        result["applied"] = True
        return result

    if labels_aligned(decision):
        result["applied"] = True
        result["skipped"] = "already_aligned"
        return result

    if dry_run:
        result["would_apply"] = True
        return result

    if decision.action == "close":
        github.sync_completed(n)
        github.close_issue(
            n,
            reason=(
                "Autonomous Dev audit: task sealed (completed). "
                f"{decision.detail}"
            ),
        )
        result["applied"] = True
        result["closed"] = True
        return result

    if decision.action == "sync_needs_fix":
        github.sync_needs_fix(n)
        result["applied"] = True
        return result

    if decision.action == "sync_product_decision":
        github.sync_product_decision(n)
        result["applied"] = True
        return result

    if decision.action == "sync_ready_for_review":
        github.sync_ready_for_review(n)
        result["applied"] = True
        return result

    if decision.action == "sync_worker_running":
        github.sync_worker_running(n)
        result["applied"] = True
        return result

    if decision.action == "sync_cursor_task_only":
        labels = github.get_issue_labels(n)
        for label in RUNTIME_LABELS:
            if label in labels and label != LABEL_COMPLETED:
                github.remove_label(n, label)
        if LABEL_CURSOR_TASK not in labels:
            github.set_issue_labels(n, {LABEL_CURSOR_TASK})
        result["applied"] = True
        return result

    return result


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    settings = get_autonomous_settings()
    token = resolve_github_token()
    if not token:
        print("FAIL: GitHub token not configured")
        return 1

    store = StateStore(settings.state_db_path)
    github = GitHubClient(settings)
    issues = _list_open_issues(settings.github_repo, token)

    decisions: list[IssueDecision] = []
    for issue in sorted(issues, key=lambda i: int(i["number"])):
        num = int(issue["number"])
        labels = {lbl["name"] for lbl in (issue.get("labels") or []) if lbl.get("name")}
        decisions.append(
            decide_issue(
                issue_number=num,
                title=str(issue.get("title") or ""),
                github_labels=labels,
                store=store,
            )
        )

    report_path = ROOT / "data" / "open_issues_audit_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    print(f"=== Open Issue Audit ({'DRY RUN' if dry_run else 'APPLY'}) ===")
    print(f"{'#':>4} {'action':22} {'task':12} {'verdict':8} detail")
    print("-" * 100)
    for d in decisions:
        aligned = labels_aligned(d)
        flag = "OK" if aligned or d.action == "keep" else "FIX"
        print(
            f"{d.issue_number:4} {d.action:22} {str(d.task_status or '-'):12} "
            f"{str(d.review_verdict or '-'):8} [{flag}] {d.detail[:50]}"
        )
        results.append(apply_decision(github, d, dry_run=dry_run))

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "open_issues": len(decisions),
        "decisions": [
            {
                "issue": d.issue_number,
                "title": d.title,
                "github_labels": sorted(d.github_labels),
                "task_status": d.task_status,
                "task_commit": d.task_commit,
                "review_verdict": d.review_verdict,
                "action": d.action,
                "detail": d.detail,
            }
            for d in decisions
        ],
        "results": results,
    }
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport: {report_path}")
    closed = sum(1 for r in results if r.get("closed"))
    synced = sum(1 for r in results if r.get("applied") and r["action"] != "keep")
    kept = sum(1 for d in decisions if d.action == "keep")
    print(f"Summary: closed={closed} synced={synced} kept={kept} total={len(decisions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
