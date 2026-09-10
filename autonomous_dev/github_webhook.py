"""GitHub webhook signature verification and payload helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WebhookVerificationError(Exception):
    """Invalid or missing webhook signature."""


def verify_github_signature(
    body: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    if not secret:
        raise WebhookVerificationError("webhook secret not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise WebhookVerificationError("missing or invalid X-Hub-Signature-256")
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise WebhookVerificationError("signature mismatch")


def parse_json_payload(body: bytes) -> dict[str, Any]:
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WebhookVerificationError("invalid JSON payload") from exc


def issue_labels(payload: dict[str, Any]) -> set[str]:
    issue = payload.get("issue") or {}
    labels = issue.get("labels") or []
    names: set[str] = set()
    for label in labels:
        if isinstance(label, dict):
            names.add(str(label.get("name", "")))
        else:
            names.add(str(label))
    names.discard("")
    return names


def is_current_cursor_task(payload: dict[str, Any]) -> bool:
    issue = payload.get("issue") or {}
    if issue.get("state") != "open":
        return False
    labels = issue_labels(payload)
    return "cursor-task" in labels and "current-task" in labels


def issue_number(payload: dict[str, Any]) -> int | None:
    issue = payload.get("issue") or {}
    num = issue.get("number")
    return int(num) if num is not None else None


def push_commit_sha(payload: dict[str, Any]) -> str | None:
    return payload.get("after") or None


def is_main_push(payload: dict[str, Any]) -> bool:
    return payload.get("ref") == "refs/heads/main"
