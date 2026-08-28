"""Tests for easydeploy-lib/python/hostfs.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import hostfs  # noqa: E402


def test_isolated_child_env_strips_parent_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/opt/engine/.venv")
    monkeypatch.setenv("VIRTUAL_ENV_PROMPT", "engine")
    monkeypatch.setenv("UV_PROJECT", "/opt/engine")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/opt/engine/.venv")
    monkeypatch.setenv("HOME", "/home/operator")
    env = hostfs.isolated_child_env()
    assert "VIRTUAL_ENV" not in env
    assert "UV_PROJECT" not in env
    assert "UV_PROJECT_ENVIRONMENT" not in env
    assert env["HOME"] == "/home/operator"


def test_isolated_child_env_does_not_mutate_caller(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/opt/engine/.venv")
    base = {"VIRTUAL_ENV": "/opt/engine/.venv", "HOME": "/home/operator"}
    env = hostfs.isolated_child_env(base)
    assert "VIRTUAL_ENV" not in env
    assert base["VIRTUAL_ENV"] == "/opt/engine/.venv"


def test_ensure_writable_directory_when_allowed(tmp_path):
    target = tmp_path / "svc" / "config"
    result = hostfs.ensure_writable_directory(target)
    assert result == target
    assert target.is_dir()


def test_ensure_writable_directory_uses_sudo_when_parent_not_writable(tmp_path, monkeypatch):
    target = tmp_path / "svc" / "config"
    runs: list[list[str]] = []

    def fake_sudo(args, *, noninteractive=False):
        runs.append(list(args))
        if args[0] == "mkdir":
            Path(args[-1]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(hostfs, "_can_create", lambda path: False)
    monkeypatch.setattr(hostfs, "_sudo", fake_sudo)

    hostfs.ensure_writable_directory(target)
    assert runs[0] == ["mkdir", "-p", str(target)]
    assert target.is_dir()


def test_privilege_hint_has_no_hardcoded_login():
    hint = hostfs._privilege_hint(Path("/var/lib/authelia"))
    assert "debian" not in hint.lower()
    assert "ubuntu" not in hint.lower()
    assert "sudo su" in hint


def test_service_uid_gid_non_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    monkeypatch.setattr(os, "getuid", lambda: 1001)
    monkeypatch.setattr(os, "getgid", lambda: 1001)
    assert hostfs.service_uid_gid(root_default=(1000, 1000)) == (1001, 1001)


def test_service_uid_gid_root_uses_image_default(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert hostfs.service_uid_gid(root_default=(1000, 1000)) == (1000, 1000)
