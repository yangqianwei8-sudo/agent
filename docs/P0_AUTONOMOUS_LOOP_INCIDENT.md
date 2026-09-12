# P0 Autonomous Loop Incident Forensics — Issue #24

Generated: 2026-09-12 (production investigation)

## Exact Stop Point

**Worker 在 `ready-for-review` 转换之前被 `recover_stale_lease()` 强制终止；commit 已 orphan 落地，Reviewer/handoff 链从未执行。**

## Task #24 Facts (task_id=51)

| Field | Value |
|-------|-------|
| execution_key | `yangqianwei8-sudo/agent#24#2026-09-11T16:56:10Z` |
| status | `failed` |
| error | `stale worker lease recovered` |
| commit_sha (DB) | **null** |
| created_at | 2026-09-11T16:56:13Z |
| updated_at (failed) | 2026-09-11T17:09:15Z |
| last progress event | 2026-09-11T17:02:44Z (COMMAND_FINISHED) |
| review_invocation | **none** |
| handoff record | **none** |

## Timeline

1. **16:56** — Worker started task 51, Cursor 大量 FILE_EDIT/COMMAND 事件
2. **17:02:44** — 最后一条 execution event
3. **17:02:48** — uvicorn 进程重启（healthz started_at），Worker 线程被杀死，lease 孤儿化
4. **17:09:15** — watchdog `recover_stale_lease()` 将 task 51 标为 FAILED（heartbeat >300s，无 progress 保护）
5. **17:09:17** — handoff 为 issue #26 尝试启动新 Worker（与 #24 未完成执行冲突）
6. **17:10:23** — git commit `ec457517` 落地（Cursor 完成 handoff 代码），**DB 仍为 failed/null**

## Root Causes

1. **Progress-blind stale lease recovery** — `recover_stale_lease()` 仅看 heartbeat TTL(300s)，未考虑 Cursor 长任务 progress events（虽已配置 `cursor_long_op_suspect_seconds=900` 但未接入 recovery）
2. **Service restart mid-worker** — 进程重启杀死 Worker 线程，lease 未释放，后续 recovery 误判
3. **Orphan commit** — Worker 线程死亡后 commit 仍可能发生，但 `transition_ready_for_review` 未写入 DB
4. **Stale GitHub label** — `worker-running` 残留，无 valid lease
5. **DEPLOYMENT_STALE** — runtime SHA `8adb9ca` != origin/main `ec457517`

## Why ec457517 Did Not Close the Loop

Handoff 代码在 commit 中，但 **Issue #24 从未进入 READY_FOR_REVIEW → Reviewer PASS → perform_handoff()**。代码存在 ≠ 链路执行。

## Fixes Applied

- `recover_stale_lease`: progress-aware lease extension (up to `cursor_long_op_suspect_seconds`)
- `append_execution_event`: extends lease heartbeat on progress
- `loop_recovery.py`: orphan push reconcile, stale label cleanup, stale DB running reconcile
- `watchdog` + `main` startup: periodic and boot recovery
- `worker.py`: revive FAILED→RUNNING before ready-for-review if push succeeded
