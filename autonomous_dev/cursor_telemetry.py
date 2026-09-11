"""Map Cursor SDK stream events to operational execution events."""

from __future__ import annotations

import json
from typing import Any

from autonomous_dev.execution_events import ExecutionEventRecorder, ExecutionEventType


def _tool_name(tool_call: dict[str, Any]) -> str:
    return str(tool_call.get("name") or tool_call.get("toolName") or "").lower()


def _tool_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    raw = tool_call.get("args") or tool_call.get("input") or tool_call.get("parameters") or {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _tool_path(args: dict[str, Any]) -> str | None:
    for key in ("path", "file_path", "filePath", "target_file", "targetFile", "relative_path"):
        val = args.get(key)
        if val:
            return str(val)
    return None


def _tool_command(args: dict[str, Any]) -> list[str] | None:
    cmd = args.get("command") or args.get("cmd")
    if isinstance(cmd, list):
        return [str(x) for x in cmd[:12]]
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip().split()[:12]
    return None


def map_tool_call_to_event(
    *,
    tool_call: dict[str, Any],
    status: str = "running",
    result_summary: str | None = None,
) -> tuple[ExecutionEventType | None, dict[str, Any]]:
    name = _tool_name(tool_call)
    args = _tool_args(tool_call)
    path = _tool_path(args)
    cmd = _tool_command(args)
    meta: dict[str, Any] = {"tool": name, "status": status}

    if any(k in name for k in ("read", "view", "cat")):
        return ExecutionEventType.FILE_READ, {"file_path": path, "metadata": meta}
    if any(k in name for k in ("edit", "write", "replace", "patch", "create")):
        if "delete" in name or "remove" in name:
            return ExecutionEventType.FILE_DELETE, {"file_path": path, "metadata": meta}
        if "create" in name:
            return ExecutionEventType.FILE_CREATE, {"file_path": path, "metadata": meta}
        return ExecutionEventType.FILE_EDIT, {"file_path": path, "metadata": meta}
    if any(k in name for k in ("delete", "remove", "unlink")):
        return ExecutionEventType.FILE_DELETE, {"file_path": path, "metadata": meta}
    if any(k in name for k in ("shell", "terminal", "bash", "run")):
        if status == "completed" and result_summary:
            return ExecutionEventType.COMMAND_FINISHED, {
                "command_summary": cmd,
                "result_summary": result_summary,
                "status": "ok",
                "metadata": meta,
            }
        return ExecutionEventType.COMMAND_STARTED, {
            "command_summary": cmd,
            "metadata": meta,
        }
    if name:
        return ExecutionEventType.CURSOR_PROGRESS, {
            "action": f"Cursor tool: {name}",
            "file_path": path,
            "command_summary": cmd,
            "result_summary": result_summary,
            "metadata": meta,
        }
    return None, {}


def record_cursor_stream_event(
    events: ExecutionEventRecorder,
    stream_event: Any,
) -> None:
    """Record one Cursor SDK RunStreamEvent if mappable."""
    interaction = getattr(stream_event, "interaction_update", None)
    if interaction is not None:
        update_type = getattr(interaction, "type", "")
        if update_type == "tool-call-started":
            tool_call = dict(getattr(interaction, "tool_call", {}) or {})
            event_type, payload = map_tool_call_to_event(tool_call=tool_call, status="running")
            if event_type:
                events.record(event_type, phase="cursor", **payload)
        elif update_type == "tool-call-completed":
            tool_call = dict(getattr(interaction, "tool_call", {}) or {})
            result = tool_call.get("result") or tool_call.get("output")
            summary = str(result)[:300] if result is not None else None
            event_type, payload = map_tool_call_to_event(
                tool_call=tool_call,
                status="completed",
                result_summary=summary,
            )
            if event_type:
                events.record(event_type, phase="cursor", **payload)

    sdk_message = getattr(stream_event, "sdk_message", None)
    if sdk_message is not None and getattr(sdk_message, "type", "") == "tool_call":
        tool_call = {
            "name": getattr(sdk_message, "name", ""),
            "args": getattr(sdk_message, "args", None),
            "result": getattr(sdk_message, "result", None),
        }
        status = str(getattr(sdk_message, "status", "running"))
        result = getattr(sdk_message, "result", None)
        summary = str(result)[:300] if result is not None else None
        event_type, payload = map_tool_call_to_event(
            tool_call=tool_call,
            status=status,
            result_summary=summary,
        )
        if event_type:
            events.record(event_type, phase="cursor", **payload)
