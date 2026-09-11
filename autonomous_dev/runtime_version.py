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


def get_runtime_version() -> dict[str, str]:
    git_sha = (os.environ.get("GIT_SHA") or "").strip() or _read_build_sha_file()
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
