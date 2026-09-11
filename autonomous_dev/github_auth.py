"""Non-interactive GitHub authentication for git push and REST API.

Priority:
1. GitHub App installation token (auto-refresh, preferred for production)
2. Fine-grained / classic PAT from GITHUB_TOKEN, GH_TOKEN, or secret file
3. SSH deploy key (GITHUB_SSH_KEY_PATH)

Never logs token values. Never uses gh auth / device flow.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_SECRET_ENV_KEYS = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
)
_SECRET_FILE_ENV_KEYS = (
    "GITHUB_TOKEN_FILE",
    "GH_TOKEN_FILE",
)
_APP_ENV_KEYS = (
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_PATH",
)

_CACHE_LOCK = threading.Lock()
_CACHED_INSTALLATION_TOKEN: str | None = None
_CACHED_INSTALLATION_EXPIRES_AT: float = 0.0


@dataclass(frozen=True)
class GitHubAuthInfo:
    mode: str
    token: str | None = None
    ssh_key_path: Path | None = None


def _read_secret_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return ""
    value = p.read_text(encoding="utf-8").strip()
    return value


def _resolve_pat_token() -> str:
    for key in _SECRET_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    for key in _SECRET_FILE_ENV_KEYS:
        file_path = os.environ.get(key, "").strip()
        if file_path:
            value = _read_secret_file(file_path)
            if value:
                return value
    for default_path in (
        "/run/secrets/github-token",
        "/run/secrets/GITHUB_TOKEN",
        "/var/run/secrets/GITHUB_TOKEN",
        os.path.expanduser("~/.secrets/github-token"),
        os.path.expanduser("~/.secrets/GITHUB_TOKEN"),
    ):
        value = _read_secret_file(default_path)
        if value:
            return value
    return ""


def _load_app_private_key() -> str:
    inline = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
    if key_path:
        return _read_secret_file(key_path)
    for default_path in (
        "/run/secrets/github-app-private-key",
        os.path.expanduser("~/.secrets/github-app-private-key.pem"),
    ):
        value = _read_secret_file(default_path)
        if value:
            return value
    return ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign_app_jwt(app_id: str, private_key_pem: str) -> str:
    import tempfile

    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 540, "iss": app_id},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem") as kf:
        kf.write(private_key_pem)
        key_file = kf.name
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file],
            input=signing_input,
            capture_output=True,
            check=True,
        )
    finally:
        Path(key_file).unlink(missing_ok=True)
    signature = _b64url(proc.stdout)
    return f"{header}.{payload}.{signature}"


def _fetch_installation_token(app_id: str, installation_id: str, private_key_pem: str) -> tuple[str, float]:
    jwt = _sign_app_jwt(app_id, private_key_pem)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    token = str(data["token"])
    expires_at = data.get("expires_at")
    if expires_at:
        # GitHub returns ISO8601; parse loosely via fromisoformat
        from datetime import datetime

        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
    else:
        exp = time.time() + 3000
    return token, exp - 120  # refresh 2 min early


def _resolve_app_token() -> str:
    global _CACHED_INSTALLATION_TOKEN, _CACHED_INSTALLATION_EXPIRES_AT

    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "").strip()
    private_key = _load_app_private_key()
    if not (app_id and installation_id and private_key):
        return ""

    with _CACHE_LOCK:
        if _CACHED_INSTALLATION_TOKEN and time.time() < _CACHED_INSTALLATION_EXPIRES_AT:
            return _CACHED_INSTALLATION_TOKEN
        token, expires_at = _fetch_installation_token(app_id, installation_id, private_key)
        _CACHED_INSTALLATION_TOKEN = token
        _CACHED_INSTALLATION_EXPIRES_AT = expires_at
        logger.info("refreshed GitHub App installation token (expires soon at epoch=%s)", int(expires_at))
        return token


def _resolve_ssh_key_path() -> Path | None:
    raw = os.environ.get("GITHUB_SSH_KEY_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_file() else None
    # Only auto-discover SSH keys when explicitly in ssh mode (avoid unregistered keys).
    if os.environ.get("GITHUB_AUTH_MODE", "").strip().lower() != "ssh":
        return None
    for default in (
        Path.home() / ".ssh" / "github_agent_deploy",
        Path("/run/secrets/github-deploy-key"),
    ):
        if default.is_file():
            return default
    return None


def resolve_github_auth() -> GitHubAuthInfo:
    """Resolve the best available non-interactive GitHub auth."""
    auth_mode = os.environ.get("GITHUB_AUTH_MODE", "").strip().lower()

    if auth_mode in ("", "app", "auto"):
        app_token = _resolve_app_token()
        if app_token:
            return GitHubAuthInfo(mode="app", token=app_token)

    if auth_mode in ("", "pat", "token", "auto"):
        pat = _resolve_pat_token()
        if pat:
            return GitHubAuthInfo(mode="pat", token=pat)

    if auth_mode in ("", "ssh", "auto"):
        ssh_key = _resolve_ssh_key_path()
        if ssh_key:
            return GitHubAuthInfo(mode="ssh", ssh_key_path=ssh_key)

    return GitHubAuthInfo(mode="none")


def resolve_github_token() -> str:
    auth = resolve_github_auth()
    return auth.token or ""


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build subprocess env for non-interactive git commands."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/false"
    env.pop("SSH_ASKPASS", None)
    env["GH_TOKEN"] = ""  # prevent gh credential helper device flow

    auth = resolve_github_auth()
    if auth.mode in ("app", "pat") and auth.token:
        env.pop("GIT_ASKPASS", None)
        env["GIT_CONFIG_COUNT"] = "2"
        env["GIT_CONFIG_KEY_0"] = "credential.helper"
        env["GIT_CONFIG_VALUE_0"] = ""
        env["GIT_CONFIG_KEY_1"] = f"url.https://x-access-token:{auth.token}@github.com/.insteadOf"
        env["GIT_CONFIG_VALUE_1"] = "https://github.com/"
    elif auth.mode == "ssh" and auth.ssh_key_path:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {auth.ssh_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
        )

    if extra:
        env.update(extra)
    return env


def register_deploy_key(
    *,
    public_key_path: Path,
    title: str = "lawyer-agent-deploy@sealos",
    read_only: bool = False,
    repo: str = "yangqianwei8-sudo/agent",
) -> None:
    """Register SSH deploy key via GitHub REST API (one-time bootstrap)."""
    token = resolve_github_token()
    if not token:
        raise RuntimeError("GITHUB_TOKEN or GitHub App credentials required to register deploy key")

    key_body = public_key_path.read_text(encoding="utf-8").strip()
    url = f"https://api.github.com/repos/{repo}/keys"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": title, "key": key_body, "read_only": read_only}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        if resp.status_code == 422 and "key is already in use" in resp.text.lower():
            logger.info("deploy key already registered")
            return
        resp.raise_for_status()
    logger.info("registered GitHub deploy key title=%s read_only=%s", title, read_only)


def clear_installation_token_cache() -> None:
    global _CACHED_INSTALLATION_TOKEN, _CACHED_INSTALLATION_EXPIRES_AT
    with _CACHE_LOCK:
        _CACHED_INSTALLATION_TOKEN = None
        _CACHED_INSTALLATION_EXPIRES_AT = 0.0
