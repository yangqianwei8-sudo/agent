#!/usr/bin/env python3
"""Generate complete, untruncated P0.2 repair evidence artifacts for Issue #20."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.acceptance_evidence import run_secret_scan  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
OUT_DIR = ROOT / "data" / "p02_repair_evidence"


def _run(label: str, args: list[str], *, timeout: int = 900) -> dict:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "label": label,
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "output": output,
    }


def _git(*args: str) -> dict:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "command": f"git {' '.join(args)}",
        "exit_code": proc.returncode,
        "output": output.strip(),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    python = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable

    evidence: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "issue": 20,
        "original_issue": 15,
        "artifacts": {},
    }

    evidence["artifacts"]["1_reviewer_bridge_unit_tests"] = _run(
        "reviewer_bridge_unit_tests",
        [python, "-m", "pytest", "backend/tests/unit/test_autonomous_dev.py", "-v", "--tb=short"],
    )
    evidence["artifacts"]["2_webhook_router_integration"] = _run(
        "webhook_router_integration",
        [
            python,
            "-m",
            "pytest",
            "backend/tests/unit/test_autonomous_dev.py",
            "-v",
            "-k",
            "webhook or router or review_bridge or push_triggers",
            "--tb=short",
        ],
    )
    evidence["artifacts"]["3_autonomous_infra_regression"] = _run(
        "autonomous_infra_regression",
        [python, "-m", "pytest", "backend/tests/unit/test_autonomous_dev.py", "-q"],
    )
    evidence["artifacts"]["4_full_pytest"] = _run(
        "full_pytest",
        [python, "-m", "pytest", "-q"],
        timeout=1200,
    )
    evidence["artifacts"]["5_ruff_check"] = _run(
        "ruff_check",
        [python, "-m", "ruff", "check", "."],
    )
    secret = run_secret_scan(ROOT)
    evidence["artifacts"]["6_secret_scan"] = {
        "passed": secret["passed"],
        "findings_count": len(secret.get("findings", [])),
        "findings": secret.get("findings", []),
        "env_tracked": secret.get("env_tracked"),
        "suspected_secrets": len(secret.get("findings", [])),
    }
    evidence["artifacts"]["7_live_acceptance"] = _run(
        "live_p02_reviewer_acceptance",
        [python, str(ROOT / "backend/scripts/live_p02_reviewer_acceptance.py")],
        timeout=300,
    )
    evidence["git"] = {
        "status": _git("status"),
        "diff_stat": _git("diff", "--stat"),
        "diff_full": _git("diff"),
        "head": _git("rev-parse", "HEAD"),
        "origin_main": _git("rev-parse", "origin/main"),
        "porcelain": _git("status", "--porcelain"),
    }
    evidence["reviewer_independence"] = {
        "llm_fallback_removed": True,
        "rationale": (
            "Reviewer uses OPENAI_API_KEY + REVIEWER_* only. "
            "LLM_API_KEY/LLM_BASE_URL/LLM_MODEL configure the worker LLM (DeepSeek) "
            "and are intentionally excluded so the independent reviewer cannot be "
            "silently substituted by the same provider the Cursor worker uses."
        ),
    }

    blocking_keys = (
        "1_reviewer_bridge_unit_tests",
        "2_webhook_router_integration",
        "3_autonomous_infra_regression",
        "4_full_pytest",
        "5_ruff_check",
        "6_secret_scan",
        "7_live_acceptance",
    )
    evidence["overall_passed"] = all(
        evidence["artifacts"][k].get("passed") is True for k in blocking_keys
    )

    json_path = OUT_DIR / f"repair_evidence_{ts}.json"
    json_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

    summary_path = OUT_DIR / f"repair_evidence_{ts}.txt"
    lines = [
        "=== P0.2 Repair Evidence (Issue #20) ===",
        f"generated_at: {evidence['generated_at']}",
        f"overall_passed: {evidence['overall_passed']}",
        "",
        "--- Reviewer independence ---",
        evidence["reviewer_independence"]["rationale"],
        "",
    ]
    for key in blocking_keys:
        art = evidence["artifacts"][key]
        lines.append(f"=== {key} ===")
        lines.append(f"passed: {art.get('passed')}")
        lines.append(f"command: {art.get('command', 'N/A')}")
        lines.append(art.get("output", json.dumps(art, indent=2)))
        lines.append("")
    lines.append("=== git ===")
    for gk, gv in evidence["git"].items():
        lines.append(f"--- {gk} ---")
        lines.append(gv.get("output", ""))
        lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Evidence JSON: {json_path}")
    print(f"Evidence summary: {summary_path}")
    print(f"overall_passed: {evidence['overall_passed']}")
    return 0 if evidence["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
