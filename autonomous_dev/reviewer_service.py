"""Independent technical reviewer — OpenAI API or deterministic acceptance mode."""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from autonomous_dev.acceptance_evidence import (
    format_evidence_for_reviewer,
    generate_acceptance_report,
    load_report,
)
from autonomous_dev.config import AutonomousDevSettings

logger = logging.getLogger(__name__)

REVIEWER_ACCEPTANCE_MARKER = "[P0.2-REVIEWER-ACCEPTANCE]"
REVIEWER_FAIL_MARKER = "[P0.2-REVIEWER-FAIL-ACCEPTANCE]"
REVIEWER_PRODUCT_DECISION_MARKER = "[P0.2-PRODUCT-DECISION-REVIEW]"
REVIEWER_FAIL_REQUIRED_MAGIC = "P0_2_FAIL_MAGIC_REQUIRED"


class ReviewerCredentialError(RuntimeError):
    """Reviewer API credentials absent — fail closed, do not substitute Cursor."""


@dataclass
class ReviewContext:
    issue_number: int
    issue_body: str
    commit_sha: str
    diff: str
    test_evidence: str


@dataclass
class ReviewResult:
    verdict: str  # PASS | FAIL | PRODUCT_DECISION
    reason: str
    invocation_id: str
    product_decision: dict[str, str] | None = None
    fail_repair_summary: str | None = None


class ReviewerService:
    def __init__(
        self,
        settings: AutonomousDevSettings,
        *,
        repo_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root or settings.repo_root

    def review(self, ctx: ReviewContext, *, invocation_id: str | None = None) -> ReviewResult:
        inv_id = invocation_id or str(uuid.uuid4())
        if self.settings.autonomous_worker_mode == "deterministic":
            return self._deterministic_review(ctx, inv_id)
        creds = self.settings.resolve_reviewer_credentials()
        if creds is None:
            raise ReviewerCredentialError(
                "OPENAI_API_KEY required for independent reviewer — "
                "credential blocker; will not substitute Cursor or worker LLM as reviewer"
            )
        return self._openai_review(ctx, inv_id, creds)

    def _deterministic_review(self, ctx: ReviewContext, inv_id: str) -> ReviewResult:
        body = ctx.issue_body
        if REVIEWER_PRODUCT_DECISION_MARKER in body:
            return ReviewResult(
                verdict="PRODUCT_DECISION",
                reason="Issue contains product-decision review marker",
                invocation_id=inv_id,
                product_decision={
                    "question": "Should acceptance use marker A or marker B format?",
                    "why_owner_required": "Issue body contains [P0.2-PRODUCT-DECISION-REVIEW] marker.",
                    "option_a": "Use format A for all acceptance markers",
                    "impact_a": "Changes acceptance convention across autonomous infra.",
                    "option_b": "Keep current marker format unchanged",
                    "impact_b": "Task remains blocked until owner confirms.",
                    "recommended_option": "B",
                    "recommendation_reason": "Avoid scope change without owner approval.",
                },
            )
        if REVIEWER_FAIL_MARKER in body:
            marker_path = self.repo_root / "autonomous_dev" / "acceptance_marker.txt"
            content = marker_path.read_text(encoding="utf-8") if marker_path.exists() else ""
            if REVIEWER_FAIL_REQUIRED_MAGIC not in content:
                return ReviewResult(
                    verdict="FAIL",
                    reason=(
                        f"acceptance_marker.txt missing required magic {REVIEWER_FAIL_REQUIRED_MAGIC}"
                    ),
                    invocation_id=inv_id,
                    fail_repair_summary=(
                        f"Add `{REVIEWER_FAIL_REQUIRED_MAGIC}` to autonomous_dev/acceptance_marker.txt"
                    ),
                )
        if REVIEWER_ACCEPTANCE_MARKER in body or "[P0-LIVE-ACCEPTANCE]" in body:
            if "acceptance_marker" in ctx.diff or "acceptance_marker.txt" in ctx.diff:
                return ReviewResult(
                    verdict="PASS",
                    reason="Harmless acceptance marker updated as required",
                    invocation_id=inv_id,
                )
            return ReviewResult(
                verdict="FAIL",
                reason="Expected acceptance_marker.txt change not found in diff",
                invocation_id=inv_id,
                fail_repair_summary="Ensure worker updates autonomous_dev/acceptance_marker.txt",
            )
        return ReviewResult(
            verdict="SKIP",
            reason="No reviewer acceptance marker — task stays ready-for-review",
            invocation_id=inv_id,
        )

    def _openai_review(
        self,
        ctx: ReviewContext,
        inv_id: str,
        creds: tuple[str, str, str],
    ) -> ReviewResult:
        api_key, base_url, model = creds
        system = (
            "You are an independent technical reviewer for an autonomous dev pipeline. "
            "Judge ONLY technical correctness. Never decide product/legal/business scope — "
            'return PRODUCT_DECISION for ambiguity. Respond JSON only: '
            '{"verdict":"PASS"|"FAIL"|"PRODUCT_DECISION","reason":"...","fail_repair_summary":"...",'
            '"product_decision":{"question":"...","why_owner_required":"...","option_a":"...",'
            '"impact_a":"...","option_b":"...","impact_b":"...","recommended_option":"...",'
            '"recommendation_reason":"..."}}'
        )
        user = (
            f"Issue #{ctx.issue_number}\n\n"
            f"--- Issue body (SSOT) ---\n{ctx.issue_body[:8000]}\n\n"
            f"--- Commit ---\n{ctx.commit_sha}\n\n"
            f"--- Diff ---\n{ctx.diff[:12000]}\n\n"
            f"--- Test evidence ---\n{ctx.test_evidence[:4000]}\n"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(max(1, self.settings.reviewer_max_retries + 1)):
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code in {429, 502, 503, 504} and attempt < self.settings.reviewer_max_retries:
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"reviewer API HTTP {resp.status_code}")
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                verdict = str(parsed.get("verdict", "FAIL")).upper()
                if verdict not in {"PASS", "FAIL", "PRODUCT_DECISION", "SKIP"}:
                    verdict = "FAIL"
                return ReviewResult(
                    verdict=verdict,
                    reason=str(parsed.get("reason", ""))[:2000],
                    invocation_id=inv_id,
                    fail_repair_summary=parsed.get("fail_repair_summary"),
                    product_decision=parsed.get("product_decision"),
                )
            except Exception as exc:  # noqa: BLE001 — retry boundary
                last_exc = exc
                if attempt < self.settings.reviewer_max_retries:
                    continue
                raise RuntimeError(f"reviewer API failed: {exc}") from exc
        raise RuntimeError(f"reviewer API failed: {last_exc}")

    def gather_context(
        self,
        *,
        issue_number: int,
        issue_body: str,
        commit_sha: str,
    ) -> ReviewContext:
        diff = self._git_diff(commit_sha)
        tests = self._gather_test_evidence(commit_sha)
        return ReviewContext(
            issue_number=issue_number,
            issue_body=issue_body,
            commit_sha=commit_sha,
            diff=diff,
            test_evidence=tests,
        )

    def _git_diff(self, commit_sha: str) -> str:
        if self.settings.autonomous_worker_mode == "deterministic":
            return f"diff --git a/autonomous_dev/acceptance_marker.txt issue #{commit_sha[:8]}"
        try:
            show = subprocess.run(
                ["git", "show", "--stat", "--patch", commit_sha],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if show.returncode == 0:
                return show.stdout[:20000]
            diff = subprocess.run(
                ["git", "diff", f"{commit_sha}^", commit_sha],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            return diff.stdout[:20000] if diff.returncode == 0 else ""
        except OSError:
            return ""

    def _gather_test_evidence(self, commit_sha: str) -> str:
        if self.settings.autonomous_worker_mode == "deterministic":
            report = load_report(self.repo_root, commit_sha)
            if report is None:
                report = generate_acceptance_report(self.repo_root, commit_sha)
            return format_evidence_for_reviewer(
                commit_sha=commit_sha,
                diff=self._git_diff(commit_sha),
                report=report,
            )
        report = load_report(self.repo_root, commit_sha)
        if report is None:
            report = generate_acceptance_report(self.repo_root, commit_sha)
        return format_evidence_for_reviewer(
            commit_sha=commit_sha,
            diff=self._git_diff(commit_sha),
            report=report,
        )
