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
    assert not (target / ".easydeploy-write-test").exists()


def test_ensure_writable_directory_escalates_when_probe_fails(tmp_path, monkeypatch):
    target = tmp_path / "config"
    runs: list[list[str]] = []
    probes = {"n": 0}

    def fake_probe(directory):
        probes["n"] += 1
        if probes["n"] == 1:
            raise PermissionError("denied")

    def fake_sudo(args, *, noninteractive=False):
        runs.append(list(args))
        if args[0] == "mkdir":
            Path(args[-1]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(hostfs, "_probe_write_dir", fake_probe)
    monkeypatch.setattr(hostfs, "_sudo", fake_sudo)

    hostfs.ensure_writable_directory(target)
    assert ["mkdir", "-p", str(target)] in runs
    assert any(cmd[:2] == ["chown", "-R"] for cmd in runs)
    assert probes["n"] == 2


def test_prepare_writable_file_creates_parent(tmp_path):
    path = tmp_path / "config" / "users_database.yml"
    result = hostfs.prepare_writable_file(path)
    assert result == path
    assert path.is_file()


def test_privilege_hint_has_no_hardcoded_login():
    hint = hostfs._privilege_hint(Path("/var/lib/authelia"))
    assert "debian" not in hint.lower()
    assert "ubuntu" not in hint.lower()
    assert "sudo su" in hint


def test_service_uid_gid_non_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    monkeypatch.setattr(os, "getegid", lambda: 1001)
    assert hostfs.service_uid_gid(root_default=(1000, 1000)) == (1001, 1001)


def test_service_uid_gid_root_uses_image_default(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert hostfs.service_uid_gid(root_default=(1000, 1000)) == (1000, 1000)
