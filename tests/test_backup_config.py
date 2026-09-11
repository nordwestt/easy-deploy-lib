"""Tests for easydeploy-lib/python/backup_config.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import backup_config  # noqa: E402


def write_deploy(tmp_path: Path, block: str) -> Path:
    path = tmp_path / "deploy.yaml"
    path.write_text(block)
    return path


def test_disabled_by_default(tmp_path):
    path = write_deploy(tmp_path, "service: {}\n")
    settings = backup_config.load_backup_settings(path)
    assert settings["enabled"] is False
    assert settings["repository"]["type"] == "local"
    assert settings["schedule"]["calendar"] == "*-*-* 03:00:00"
    assert settings["retention"]["keep_daily"] == 7


def test_local_repository_requires_absolute_path(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  enabled: true\n  repository:\n    path: relative/dir\n",
    )
    with pytest.raises(backup_config.BackupConfigError, match="absolute path"):
        backup_config.load_backup_settings(path)


def test_local_repository_valid(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  enabled: true\n  repository:\n    path: /var/backups/kit\n",
    )
    settings = backup_config.load_backup_settings(path)
    assert settings["repository"]["path"] == "/var/backups/kit"


def test_sftp_requires_host_user_key(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  enabled: true\n  repository:\n    type: sftp\n    path: /repos/kit\n",
    )
    with pytest.raises(backup_config.BackupConfigError, match="host is required"):
        backup_config.load_backup_settings(path)


def test_sftp_requires_existing_ssh_key(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  enabled: true\n  repository:\n    type: sftp\n"
        "    host: backup.example.com\n    user: borg\n    path: /repos/kit\n"
        "    ssh_key_path: /nonexistent/key\n",
    )
    with pytest.raises(backup_config.BackupConfigError, match="ssh_key_path not found"):
        backup_config.load_backup_settings(path)


def test_invalid_encryption_rejected(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  repository:\n    encryption: bogus\n",
    )
    with pytest.raises(backup_config.BackupConfigError, match="encryption"):
        backup_config.load_backup_settings(path)


def test_invalid_type_rejected(tmp_path):
    path = write_deploy(tmp_path, "backup:\n  repository:\n    type: tape\n")
    with pytest.raises(backup_config.BackupConfigError, match="'local' or 'sftp'"):
        backup_config.load_backup_settings(path)


def test_borg_repo_url_local_and_sftp(tmp_path):
    key = tmp_path / "key"
    key.write_text("")
    local = write_deploy(tmp_path, "backup:\n  repository:\n    path: /var/backups/kit\n")
    settings = backup_config.load_backup_settings(local)
    assert backup_config.borg_repo_url(settings) == "/var/backups/kit"

    sftp = write_deploy(
        tmp_path,
        "backup:\n  repository:\n    type: sftp\n    host: backup.example.com\n"
        "    user: borg\n    path: /repos/kit\n"
        f"    ssh_key_path: {key}\n",
    )
    settings = backup_config.load_backup_settings(sftp)
    assert backup_config.borg_repo_url(settings) == "ssh://borg@backup.example.com/repos/kit"


def test_borg_repo_url_includes_nonstandard_port(tmp_path):
    key = tmp_path / "key"
    key.write_text("")
    path = write_deploy(
        tmp_path,
        "backup:\n  repository:\n    type: sftp\n    host: backup.example.com\n"
        "    user: borg\n    port: 2222\n    path: /repos/kit\n"
        f"    ssh_key_path: {key}\n",
    )
    settings = backup_config.load_backup_settings(path)
    assert backup_config.borg_repo_url(settings) == "ssh://borg@backup.example.com:2222/repos/kit"


def test_borg_rsh_host_key_checking(tmp_path):
    key = tmp_path / "key"
    key.write_text("")
    strict = write_deploy(
        tmp_path,
        "backup:\n  repository:\n    type: sftp\n    host: h\n    user: u\n    path: /r\n"
        f"    ssh_key_path: {key}\n",
    )
    settings = backup_config.load_backup_settings(strict)
    assert "StrictHostKeyChecking=accept-new" in backup_config.borg_rsh(settings)

    lax = write_deploy(
        tmp_path,
        "backup:\n  repository:\n    type: sftp\n    host: h\n    user: u\n    path: /r\n"
        "    host_key_check: false\n"
        f"    ssh_key_path: {key}\n",
    )
    settings = backup_config.load_backup_settings(lax)
    rsh = backup_config.borg_rsh(settings)
    assert "StrictHostKeyChecking=no" in rsh
    assert "-p" not in rsh  # port 22 omitted


def test_emit_shell_exports(tmp_path):
    path = write_deploy(
        tmp_path,
        "backup:\n  enabled: true\n  repository:\n    path: /var/backups/kit\n"
        "    encryption: none\n  schedule:\n    enabled: true\n  retention:\n"
        "    keep_daily: 3\n",
    )
    settings = backup_config.load_backup_settings(path)
    shell = backup_config._emit_shell(settings)
    values = dict(line.split("=", 1) for line in shell.splitlines())
    assert values["BACKUP_ENABLED"] == "true"
    assert values["BACKUP_REPO_TYPE"] == "local"
    assert values["BACKUP_REPO_URL"] == "/var/backups/kit"
    assert values["BACKUP_REPO_ENCRYPTION"] == "none"
    assert values["BACKUP_SCHEDULE_ENABLED"] == "true"
    assert values["BACKUP_KEEP_DAILY"] == "3"
