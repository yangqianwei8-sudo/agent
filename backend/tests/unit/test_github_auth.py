"""GitHub non-interactive auth tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from autonomous_dev.github_auth import (
    clear_installation_token_cache,
    git_env,
    resolve_github_auth,
    resolve_github_token,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_installation_token_cache()
    yield
    clear_installation_token_cache()


def test_pat_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_value")
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    auth = resolve_github_auth()
    assert auth.mode == "pat"
    assert auth.token == "ghp_test_token_value"
    assert resolve_github_token() == "ghp_test_token_value"


def test_pat_from_secret_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("github_pat_test_file_token\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(token_file))
    auth = resolve_github_auth()
    assert auth.mode == "pat"
    assert auth.token == "github_pat_test_file_token"


def test_ssh_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    key = tmp_path / "deploy_key"
    key.write_text("fake-key\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_SSH_KEY_PATH", str(key))
    monkeypatch.setenv("GITHUB_AUTH_MODE", "ssh")
    auth = resolve_github_auth()
    assert auth.mode == "ssh"
    assert auth.ssh_key_path == key


def test_git_env_disables_interactive_prompts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    env = git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # PAT mode uses URL credential rewrite instead of GIT_ASKPASS=/bin/false
    assert "GIT_ASKPASS" not in env or env["GIT_ASKPASS"] == "/bin/false"
    assert "x-access-token:ghp_test@github.com/" in env["GIT_CONFIG_KEY_1"]


def test_no_auth_when_unconfigured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_AUTH_MODE", "pat")
    for key in list(os.environ):
        if key.startswith("GITHUB") or key in ("GH_TOKEN",):
            if key != "GITHUB_AUTH_MODE":
                monkeypatch.delenv(key, raising=False)
    auth = resolve_github_auth()
    assert auth.mode == "none"
    assert resolve_github_token() == ""
