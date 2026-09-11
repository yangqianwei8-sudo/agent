"""Machine-generated acceptance evidence tied to an exact commit SHA."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),
    re.compile(r"\bghp_[a-zA-Z0-9]{20,}\b"),
    re.compile(r"\bgho_[a-zA-Z0-9]{20,}\b"),
    re.compile(r"\bghu_[a-zA-Z0-9]{20,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password)\s*=\s*['\"]"
        r"(?!test|fake|example|changeme|your_)[a-zA-Z0-9_\-]{24,}['\"]"
    ),
)

_SKIP_PATH_PARTS = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    "data/acceptance_reports",
    "backend/tests",
    "backend/scripts",
}


def report_path(repo_root: Path, commit_sha: str) -> Path:
    return repo_root / "data" / "acceptance_reports" / f"{commit_sha[:12]}.json"


def load_report(repo_root: Path, commit_sha: str) -> dict[str, Any] | None:
    path = report_path(repo_root, commit_sha)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_cmd(args: list[str], *, cwd: Path, timeout: int = 600) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "command": " ".join(args),
            "exit_code": proc.returncode,
            "output_tail": output[-8000:],
            "passed": proc.returncode == 0,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": " ".join(args),
            "exit_code": -1,
            "output_tail": str(exc)[:2000],
            "passed": False,
        }


def _should_scan(path: Path, repo_root: Path) -> bool:
    rel = str(path.relative_to(repo_root)).replace("\\", "/")
    for skip in _SKIP_PATH_PARTS:
        if rel == skip or rel.startswith(f"{skip}/"):
            return False
    return True


def run_secret_scan(repo_root: Path) -> dict[str, Any]:
    findings: list[str] = []
    scan_roots = [repo_root / "autonomous_dev", repo_root / "backend"]
    for root in scan_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or not _should_scan(path, repo_root):
                continue
            if path.suffix in {".pyc", ".png", ".jpg", ".pdf", ".db", ".sqlite"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(path.relative_to(repo_root))
            if rel == ".env":
                continue
            for pattern in _SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{rel}: pattern {pattern.pattern[:40]}")
                    break
    env_path = repo_root / ".env"
    env_tracked = False
    if env_path.exists():
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", ".env"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            env_tracked = tracked.returncode == 0
        except OSError:
            env_tracked = False
    return {
        "passed": not findings and not env_tracked,
        "findings": findings[:20],
        "env_tracked": env_tracked,
    }


def generate_acceptance_report(
    repo_root: Path,
    commit_sha: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    existing = load_report(repo_root, commit_sha)
    if existing and not force:
        return existing

    python = sys.executable
    report: dict[str, Any] = {
        "commit_sha": commit_sha,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "local_machine",
    }
    report["pytest_focused"] = _run_cmd(
        [python, "-m", "pytest", "backend/tests/unit/test_autonomous_dev.py", "-q"],
        cwd=repo_root,
    )
    report["pytest_full"] = _run_cmd(
        [python, "-m", "pytest", "-q"],
        cwd=repo_root,
        timeout=900,
    )
    report["ruff"] = _run_cmd([python, "-m", "ruff", "check", "."], cwd=repo_root)
    report["secret_scan"] = run_secret_scan(repo_root)
    report["overall_passed"] = all(
        report[key].get("passed") is True
        for key in ("pytest_focused", "pytest_full", "ruff", "secret_scan")
    )

    path = report_path(repo_root, commit_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def format_evidence_for_reviewer(
    *,
    commit_sha: str,
    diff: str,
    report: dict[str, Any] | None,
) -> str:
    lines = [
        f"commit_sha: {commit_sha}",
        "",
        "--- diff (truncated) ---",
        diff[:12000],
        "",
    ]
    if report is None:
        lines.append("acceptance_report: not found for commit")
        return "\n".join(lines)

    lines.extend(
        [
            f"acceptance_report_source: {report.get('source', 'unknown')}",
            f"acceptance_report_generated_at: {report.get('generated_at', '')}",
            f"acceptance_overall_passed: {report.get('overall_passed')}",
            "",
        ]
    )
    for key in ("pytest_focused", "pytest_full", "ruff", "secret_scan"):
        section = report.get(key)
        if not isinstance(section, dict):
            continue
        lines.append(f"--- {key} ---")
        lines.append(f"passed: {section.get('passed')}")
        lines.append(f"command: {section.get('command', section.get('findings', ''))}")
        tail = section.get("output_tail") or section.get("findings")
        if tail:
            lines.append(str(tail)[:3000])
        lines.append("")
    return "\n".join(lines)
