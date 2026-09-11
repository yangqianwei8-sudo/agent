"""Autonomous development dashboard payload builder and HTML UI."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import UTC, datetime
from typing import Any

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.state import StateStore, TaskRecord
from autonomous_dev.status_deriver import (
    WorkerLeaseSnapshot,
    derive_current_phase,
    derive_pipeline_stages,
    derive_system_status,
    is_worker_runtime_stale,
    parse_generation,
    redact_secrets,
    sanitize_for_json,
    summarize_error,
)

logger = logging.getLogger(__name__)

DASHBOARD_GITHUB_TIMEOUT_SECONDS = 1.5
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dashboard-io")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(started_at: str | None, *, now: datetime) -> int | None:
    start = _parse_ts(started_at)
    if start is None:
        return None
    return max(0, int((now - start).total_seconds()))


def _fetch_issue_labels_sync(settings: AutonomousDevSettings, issue_number: int) -> list[str]:
    from autonomous_dev.github_client import GitHubClient

    client = GitHubClient(settings)
    return sorted(client.get_issue_labels(issue_number))


def _fetch_issue_labels_bounded(
    settings: AutonomousDevSettings,
    issue_number: int | None,
    *,
    timeout_seconds: float = DASHBOARD_GITHUB_TIMEOUT_SECONDS,
) -> tuple[list[str], str | None]:
    if issue_number is None:
        return [], None
    future = _executor.submit(_fetch_issue_labels_sync, settings, issue_number)
    try:
        return future.result(timeout=timeout_seconds), None
    except FuturesTimeout:
        future.cancel()
        return [], "github_labels_timeout"
    except Exception as exc:  # noqa: BLE001 — degrade, never fail dashboard
        logger.debug("dashboard github labels failed issue=%s: %s", issue_number, exc)
        return [], "github_labels_unavailable"


def _activity_from_snapshot(
    delivery_rows: list[dict[str, Any]],
    task_rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in delivery_rows:
        action = row.get("action") or ""
        summary = f"Webhook 收到 {row.get('event_type') or 'unknown'}"
        if action:
            summary += f" / {action}"
        summary += f" ({row.get('status')})"
        events.append(
            {
                "at": row.get("received_at"),
                "kind": "webhook_received",
                "summary": redact_secrets(summary),
            }
        )

    for row in task_rows:
        status = row.get("status")
        issue_number = row.get("issue_number")
        kind = "task_update"
        summary = f"Issue #{issue_number} 任务状态 → {status}"
        if status == "running":
            kind = "worker_started"
            summary = f"Issue #{issue_number} Worker 已启动"
        elif status == "ready-for-review":
            kind = "ready_for_review"
            summary = f"Issue #{issue_number} 进入 ready-for-review"
        elif status == "completed":
            kind = "verdict"
            summary = f"Issue #{issue_number} 已完成"
        elif status == "failed":
            kind = "failure"
            summary = f"Issue #{issue_number} 失败"
        elif status == "product-decision":
            kind = "waiting_user"
            summary = f"Issue #{issue_number} 等待产品决策"
        events.append(
            {
                "at": row.get("updated_at"),
                "kind": kind,
                "summary": redact_secrets(summary),
            }
        )

    events.sort(key=lambda item: item.get("at") or "", reverse=True)
    return events[:limit]


def _task_to_dict(
    task: TaskRecord | None,
    *,
    settings: AutonomousDevSettings,
    system_status: Any,
    lease: WorkerLeaseSnapshot,
    now: datetime,
    labels: list[str],
) -> dict[str, Any] | None:
    if task is None:
        return None
    started_at = task.created_at
    return {
        "repo": settings.github_repo,
        "issue_number": task.issue_number,
        "title": None,
        "task_id": task.id,
        "generation": parse_generation(task.execution_key),
        "task_status": task.status.value,
        "executor": settings.autonomous_worker_mode,
        "current_phase": derive_current_phase(
            task,
            system_status=system_status,
            lease=lease,
        ),
        "started_at": started_at,
        "elapsed_seconds": _elapsed_seconds(started_at, now=now),
        "commit_sha": task.commit_sha,
        "labels": labels,
        "error_summary": summarize_error(task.error),
    }


def build_dashboard_payload(
    settings: AutonomousDevSettings,
    store: StateStore,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC)
    degraded_reasons: list[str] = []
    snapshot = store.load_dashboard_snapshot(activity_limit=15, completed_limit=5)

    lease_raw = snapshot["lease"]
    lease = WorkerLeaseSnapshot(
        locked=lease_raw.locked,
        owner=lease_raw.owner,
        task_id=lease_raw.task_id,
        issue_number=lease_raw.issue_number,
        acquired_at=lease_raw.acquired_at,
        heartbeat_at=lease_raw.heartbeat_at,
        lease_expires_at=lease_raw.lease_expires_at,
    )
    primary_task = snapshot["primary_task"]
    current_task = snapshot["current_task"]
    active_review = snapshot["active_review"]
    reviewer_locked = snapshot["reviewer_locked"]
    worker_locked = snapshot["worker_locked"]

    system_status = derive_system_status(
        primary_task=primary_task,
        lease=lease,
        reviewer_locked=reviewer_locked,
        active_review=active_review,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=generated_at,
        recent_failed_task=snapshot["recent_failed"],
    )

    labels: list[str] = []
    label_issue = current_task.issue_number if current_task else None
    fetched_labels, label_reason = _fetch_issue_labels_bounded(settings, label_issue)
    if label_reason:
        degraded_reasons.append(label_reason)
    else:
        labels = fetched_labels

    heartbeat_at = lease.heartbeat_at
    heartbeat_age: int | None = None
    hb_ts = _parse_ts(heartbeat_at)
    if hb_ts is not None:
        heartbeat_age = max(0, int((generated_at - hb_ts).total_seconds()))

    runtime_stale = False
    if primary_task is not None:
        runtime_stale = is_worker_runtime_stale(
            primary_task,
            lease,
            lease_ttl_seconds=settings.worker_lease_ttl_seconds,
            now=generated_at,
        )

    latest_error = None
    if primary_task and primary_task.error:
        latest_error = summarize_error(primary_task.error)
    elif active_review and active_review.error:
        latest_error = summarize_error(active_review.error)

    owner_attention = {
        "required": system_status.value == "WAITING_USER",
        "message": (
            summarize_error(primary_task.error)
            if primary_task and system_status.value == "WAITING_USER"
            else "无须处理"
        ),
    }

    recent_completed = [
        {
            "issue_number": row["issue_number"],
            "title": None,
            "verdict": row["review_verdict"] or "PASS",
            "commit_sha": row["commit_sha"],
            "completed_at": row["updated_at"],
        }
        for row in snapshot["completed_rows"]
    ]

    payload: dict[str, Any] = {
        "system_status": system_status.value,
        "current_task": _task_to_dict(
            current_task,
            settings=settings,
            system_status=system_status,
            lease=lease,
            now=generated_at,
            labels=labels,
        ),
        "pipeline_stages": derive_pipeline_stages(
            current_task or primary_task,
            system_status=system_status,
            lease=lease,
            active_review=active_review,
        ),
        "runtime_evidence": {
            "worker_locked": worker_locked,
            "worker_lease_acquired_at": lease.acquired_at,
            "worker_lease_expires_at": lease.lease_expires_at,
            "last_worker_heartbeat": heartbeat_at,
            "heartbeat_age_seconds": heartbeat_age,
            "reviewer_configured": settings.resolve_reviewer_credentials() is not None,
            "reviewer_locked": reviewer_locked,
            "reviewer_status": (
                active_review.status.value
                if active_review
                else ("locked" if reviewer_locked else "idle")
            ),
            "last_reviewer_invocation": (
                {
                    "invocation_id": active_review.invocation_id,
                    "status": active_review.status.value,
                    "verdict": active_review.verdict.value if active_review.verdict else None,
                    "commit_sha": active_review.commit_sha,
                }
                if active_review
                else None
            ),
            "latest_error_summary": latest_error,
            "runtime_stale": runtime_stale,
            "worker_mode": settings.autonomous_worker_mode,
            "dashboard_degraded": bool(degraded_reasons),
            "degraded_reason": ", ".join(degraded_reasons) if degraded_reasons else None,
            "payload_generated_at": generated_at.isoformat(),
        },
        "recent_activity": _activity_from_snapshot(
            snapshot["delivery_rows"],
            snapshot["task_rows"],
            limit=15,
        ),
        "owner_attention": owner_attention,
        "recent_completed": recent_completed,
        "last_updated": generated_at.isoformat(),
    }
    return sanitize_for_json(payload)


def build_recent_activity(store: StateStore, *, limit: int = 20) -> list[dict[str, Any]]:
    snapshot = store.load_dashboard_snapshot(activity_limit=limit, completed_limit=1)
    return _activity_from_snapshot(
        snapshot["delivery_rows"],
        snapshot["task_rows"],
        limit=limit,
    )


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自主开发 Dashboard</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 16px; background: #0f1419; color: #e7ecf3; }
    h1 { margin: 0 0 8px; font-size: 1.5rem; }
    .muted { color: #9aa7b5; font-size: 0.9rem; }
    .status-banner { padding: 16px; border-radius: 10px; margin: 16px 0; font-size: 1.25rem; font-weight: 700; }
    .status-RUNNING { background: #14532d; color: #bbf7d0; }
    .status-REVIEWING { background: #1e3a5f; color: #bfdbfe; }
    .status-WAITING_USER { background: #713f12; color: #fde68a; }
    .status-FAILED { background: #7f1d1d; color: #fecaca; }
    .status-STALE { background: #78350f; color: #fed7aa; }
    .status-IDLE { background: #334155; color: #cbd5e1; }
    section { background: #1a2332; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
    section h2 { margin: 0 0 10px; font-size: 1.05rem; }
    .grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    .kv { background: #111827; padding: 8px 10px; border-radius: 8px; }
    .kv .k { color: #94a3b8; font-size: 0.8rem; }
    .kv .v { word-break: break-all; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #334155; vertical-align: top; }
    .pipeline { display: flex; flex-wrap: wrap; gap: 8px; }
    .stage { padding: 6px 10px; border-radius: 999px; font-size: 0.85rem; background: #111827; }
    .stage.pass { background: #14532d; }
    .stage.running { background: #1e3a5f; }
    .stage.fail { background: #7f1d1d; }
    .stage.stale { background: #78350f; }
    .stage.pending { background: #374151; }
    .stage.unknown { background: #1f2937; color: #9ca3af; }
    ul { margin: 0; padding-left: 18px; }
    .error { color: #fca5a5; }
    .degraded { color: #fed7aa; font-size: 0.85rem; margin-top: 6px; }
    @media (max-width: 640px) { body { padding: 10px; } .status-banner { font-size: 1.05rem; } }
  </style>
</head>
<body>
  <h1>自主开发 Dashboard</h1>
  <div class="muted">只读运行视图 · 每 7 秒自动刷新 JSON</div>
  <div id="status-banner" class="status-banner status-IDLE">加载中…</div>
  <div class="muted" id="last-updated"></div>
  <div id="degraded" class="degraded"></div>

  <section>
    <h2>当前任务</h2>
    <div id="current-task" class="grid"></div>
  </section>

  <section>
    <h2>Pipeline 进度</h2>
    <div id="pipeline" class="pipeline"></div>
  </section>

  <section>
    <h2>Runtime 证据</h2>
    <div id="runtime" class="grid"></div>
  </section>

  <section>
    <h2>Owner Attention</h2>
    <div id="owner-attention"></div>
  </section>

  <section>
    <h2>Recent Activity</h2>
    <ul id="activity"></ul>
  </section>

  <section>
    <h2>Recent Completed</h2>
    <table>
      <thead><tr><th>Issue</th><th>Verdict</th><th>Commit</th><th>Completed</th></tr></thead>
      <tbody id="completed"></tbody>
    </table>
  </section>

  <script>
    const STATUS_LABELS = {
      RUNNING: "运行中",
      REVIEWING: "审查中",
      WAITING_USER: "等待用户决策",
      FAILED: "失败",
      STALE: "运行时过期",
      IDLE: "空闲",
    };

    function kv(key, value) {
      return `<div class="kv"><div class="k">${key}</div><div class="v">${value ?? "—"}</div></div>`;
    }

    function render(data) {
      const status = data.system_status || "IDLE";
      const banner = document.getElementById("status-banner");
      banner.className = `status-banner status-${status}`;
      banner.textContent = `${STATUS_LABELS[status] || status} (${status})`;
      document.getElementById("last-updated").textContent = `最后更新: ${data.last_updated || "—"}`;

      const rt = data.runtime_evidence || {};
      const degradedEl = document.getElementById("degraded");
      if (rt.dashboard_degraded) {
        degradedEl.textContent = `部分外部数据降级: ${rt.degraded_reason || "unknown"}`;
      } else {
        degradedEl.textContent = "";
      }

      const task = data.current_task;
      const taskEl = document.getElementById("current-task");
      if (!task) {
        taskEl.innerHTML = kv("状态", "无当前活动任务");
      } else {
        taskEl.innerHTML = [
          kv("Repo", task.repo),
          kv("Issue", `#${task.issue_number}`),
          kv("标题", task.title),
          kv("Task ID", task.task_id),
          kv("Generation", task.generation),
          kv("Task Status", task.task_status),
          kv("Executor", task.executor),
          kv("Current Phase", task.current_phase),
          kv("Started At", task.started_at),
          kv("Elapsed (s)", task.elapsed_seconds),
          kv("Commit SHA", task.commit_sha),
          kv("Labels", (task.labels || []).join(", ")),
          kv("Error", task.error_summary),
        ].join("");
      }

      const pipelineEl = document.getElementById("pipeline");
      pipelineEl.innerHTML = (data.pipeline_stages || []).map(
        (s) => `<span class="stage ${s.state}">${s.label}: ${s.state}</span>`
      ).join("");

      document.getElementById("runtime").innerHTML = [
        kv("Worker Locked", rt.worker_locked),
        kv("Lease Acquired", rt.worker_lease_acquired_at),
        kv("Lease Expires", rt.worker_lease_expires_at),
        kv("Last Heartbeat", rt.last_worker_heartbeat),
        kv("Heartbeat Age (s)", rt.heartbeat_age_seconds),
        kv("Reviewer Configured", rt.reviewer_configured),
        kv("Reviewer Locked", rt.reviewer_locked),
        kv("Reviewer Status", rt.reviewer_status),
        kv("Runtime Stale", rt.runtime_stale),
        kv("Worker Mode", rt.worker_mode),
        kv("Latest Error", rt.latest_error_summary),
        kv("Payload Generated", rt.payload_generated_at),
      ].join("");

      const owner = data.owner_attention || {};
      document.getElementById("owner-attention").innerHTML = owner.required
        ? `<div class="error">需要你决定: ${owner.message || "请查看 Issue"}</div>`
        : `<div>${owner.message || "无须处理"}</div>`;

      const activityEl = document.getElementById("activity");
      activityEl.innerHTML = (data.recent_activity || []).map(
        (a) => `<li><span class="muted">${a.at || ""}</span> — ${a.summary || a.kind}</li>`
      ).join("");

      const completedEl = document.getElementById("completed");
      completedEl.innerHTML = (data.recent_completed || []).map(
        (c) => `<tr><td>#${c.issue_number}</td><td>${c.verdict || "—"}</td><td>${c.commit_sha || "—"}</td><td>${c.completed_at || "—"}</td></tr>`
      ).join("");
    }

    async function refresh() {
      try {
        const resp = await fetch("/autonomous/status.json", { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        render(await resp.json());
      } catch (err) {
        document.getElementById("status-banner").textContent = `加载失败: ${err}`;
      }
    }

    refresh();
    setInterval(refresh, 7000);
  </script>
</body>
</html>
"""
