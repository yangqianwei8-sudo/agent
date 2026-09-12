"""Runtime build/version metadata for health checks and deployment verification."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

_STARTED_AT = datetime.now(UTC).isoformat()
_BUILD_SHA_FILE = Path("/app/.git_sha")


def _read_build_sha_file() -> str:
    try:
        if _BUILD_SHA_FILE.is_file():
            return _BUILD_SHA_FILE.read_text(encoding="utf-8").strip()[:64]
    except OSError:
        return ""
    return ""


def _read_git_head(repo_root: Path | None = None) -> str:
    root = repo_root or Path(__file__).resolve().parents[1]
    head_file = root / ".git" / "HEAD"
    try:
        if not head_file.is_file():
            return ""
        ref = head_file.read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            ref_path = root / ".git" / ref[5:].strip()
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8").strip()[:64]
            return ""
        return ref[:64]
    except OSError:
        return ""


def _prefer_live_git_head() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    flag = os.environ.get("AUTONOMOUS_RUNTIME_PREFER_GIT_HEAD", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    return bool(os.environ.get("DEVBOX_JWT_SECRET"))


def get_runtime_version(*, repo_root: Path | None = None) -> dict[str, str]:
    root = repo_root or Path(__file__).resolve().parents[1]
    env_sha = (os.environ.get("GIT_SHA") or "").strip() or _read_build_sha_file()
    live_sha = _read_git_head(root) if _prefer_live_git_head() else ""
    if live_sha and (not env_sha or env_sha != live_sha):
        git_sha = live_sha
        image_tag = git_sha
    else:
        git_sha = env_sha or live_sha
        image_tag = (os.environ.get("IMAGE_TAG") or "").strip() or git_sha
    return {
        "git_sha": git_sha or "unknown",
        "image_tag": image_tag or "unknown",
        "started_at": _STARTED_AT,
        "build_time": (os.environ.get("BUILD_TIME") or "").strip(),
    }


def derive_deployment_status(*, runtime_sha: str, main_sha: str | None) -> str:
    if not runtime_sha or runtime_sha == "unknown":
        return "UNKNOWN"
    if not main_sha:
        return "UNKNOWN"
    rt = runtime_sha.strip().lower()
    mn = main_sha.strip().lower()
    if rt == mn or rt.startswith(mn[:7]) or mn.startswith(rt[:7]):
        return "CURRENT"
    return "STALE"
