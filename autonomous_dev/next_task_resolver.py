"""Resolve the next technical task after reviewer PASS — no product scope invention."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from autonomous_dev.github_client import (
    LABEL_COMPLETED,
    LABEL_CURRENT_TASK,
    LABEL_CURSOR_TASK,
    LABEL_NEEDS_FIX,
    LABEL_PRODUCT_DECISION,
    GitHubClient,
)
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER

logger = logging.getLogger(__name__)

NEXT_TASK_MARKER = "NEXT_TASK:"
ROADMAP_NEXT_MARKER = "ROADMAP_NEXT:"

_EXCLUDED_QUEUE_LABELS = frozenset(
    {
        LABEL_CURRENT_TASK,
        LABEL_COMPLETED,
        LABEL_NEEDS_FIX,
        LABEL_PRODUCT_DECISION,
        "worker-running",
        "ready-for-review",
    }
)


class NextTaskOutcome(StrEnum):
    ACTIVATE_EXISTING = "activate_existing"
    CREATE_NEW = "create_new"
    WAITING_PRODUCT = "waiting_product"
    NO_NEXT_DEFINED = "no_next_defined"


@dataclass(frozen=True)
class NextTaskResolution:
    outcome: NextTaskOutcome
    issue_number: int | None = None
    title: str | None = None
    body: str | None = None
    idempotency_key: str | None = None
    reason: str = ""


def _parse_marker_line(body: str, marker: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker):
            value = stripped.split(marker, 1)[1].strip()
            return value or None
    return None


def _stable_idempotency_key(*, source_issue: int, title: str) -> str:
    digest = hashlib.sha256(f"{source_issue}:{title}".encode()).hexdigest()[:16]
    return f"roadmap-issue:{source_issue}:{digest}"


def _build_roadmap_issue_body(
    *,
    source_issue_number: int,
    stable_key: str,
    next_title: str,
    source_body: str,
) -> str:
    footer = (
        f"Auto-materialized from roadmap after PASS on issue #{source_issue_number}.\n\n"
        f"Idempotency-Key: `{stable_key}`"
    )
    if next_title.startswith("[ACCEPT]") or REVIEWER_ACCEPTANCE_MARKER in source_body:
        return (
            f"{REVIEWER_ACCEPTANCE_MARKER}\n"
            "[P0-LIVE-ACCEPTANCE]\n"
            f"Harmless marker-only change for {next_title}.\n"
            "Update autonomous_dev/acceptance_marker.txt only.\n\n"
            f"{footer}"
        )
    return footer


class NextTaskResolver:
    """Prefer queued cursor-task issues; materialize roadmap items exactly once."""

    def __init__(self, github: GitHubClient) -> None:
        self._github = github

    def resolve(
        self,
        *,
        source_issue_number: int,
        source_body: str,
        idempotency_key: str,
    ) -> NextTaskResolution:
        next_title = _parse_marker_line(source_body, NEXT_TASK_MARKER)
        if not next_title:
            next_title = _parse_marker_line(source_body, ROADMAP_NEXT_MARKER)

        if next_title:
            existing = self._github.find_open_issue_by_title_prefix(next_title[:60])
            if existing is not None and existing != source_issue_number:
                return NextTaskResolution(
                    outcome=NextTaskOutcome.ACTIVATE_EXISTING,
                    issue_number=existing,
                    idempotency_key=idempotency_key,
                    reason=f"roadmap marker matched issue #{existing}",
                )
            stable_key = _stable_idempotency_key(
                source_issue=source_issue_number,
                title=next_title,
            )
            by_key = self._github.find_open_issue_by_body_marker(stable_key)
            if by_key is not None:
                return NextTaskResolution(
                    outcome=NextTaskOutcome.ACTIVATE_EXISTING,
                    issue_number=by_key,
                    idempotency_key=stable_key,
                    reason=f"idempotent roadmap issue #{by_key}",
                )
            return NextTaskResolution(
                outcome=NextTaskOutcome.CREATE_NEW,
                title=next_title,
                body=_build_roadmap_issue_body(
                    source_issue_number=source_issue_number,
                    stable_key=stable_key,
                    next_title=next_title,
                    source_body=source_body,
                ),
                idempotency_key=stable_key,
                reason="roadmap marker requires new issue",
            )

        queued = self._list_queued_cursor_tasks(excluding_issue=source_issue_number)
        if len(queued) == 1:
            num = queued[0]
            logger.info(
                "next-task resolver: queued issue #%s for source #%s",
                num,
                source_issue_number,
            )
            return NextTaskResolution(
                outcome=NextTaskOutcome.ACTIVATE_EXISTING,
                issue_number=num,
                idempotency_key=idempotency_key,
                reason="single queued cursor-task",
            )
        if len(queued) > 1:
            titles = ", ".join(f"#{n}" for n in queued[:5])
            return NextTaskResolution(
                outcome=NextTaskOutcome.WAITING_PRODUCT,
                reason=f"ambiguous queued cursor-task issues: {titles}",
            )

        if re.search(r"\bV2-P5\b|\bnext phase\b", source_body, re.IGNORECASE):
            return NextTaskResolution(
                outcome=NextTaskOutcome.WAITING_PRODUCT,
                reason="roadmap references undefined next product phase",
            )

        return NextTaskResolution(
            outcome=NextTaskOutcome.NO_NEXT_DEFINED,
            reason="no queued cursor-task and no roadmap marker on sealed task",
        )

    def _list_queued_cursor_tasks(self, *, excluding_issue: int) -> list[int]:
        issues = self._github.list_open_issues_with_label(LABEL_CURSOR_TASK, limit=50)
        queued: list[int] = []
        for issue in issues:
            num = int(issue["number"])
            if num == excluding_issue:
                continue
            labels = {lbl["name"] for lbl in (issue.get("labels") or []) if isinstance(lbl, dict)}
            if LABEL_CURSOR_TASK not in labels:
                continue
            if labels & _EXCLUDED_QUEUE_LABELS:
                continue
            queued.append(num)
        queued.sort()
        return queued
