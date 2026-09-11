#!/usr/bin/env python3
"""Backup configuration loader/validator for the shared `backup:` block.

Schema (deploy.yaml / engine.yaml):

    backup:
      enabled: false
      repository:
        type: local            # local | sftp
        path: /var/backups/<service>
        # sftp only:
        host: backup.example.com
        user: borg
        port: 22
        ssh_key_path: /root/.ssh/borg_backup
        host_key_check: true   # true -> accept-new, false -> no
        encryption: repokey    # repokey | none (applied only at repo creation)
      schedule:
        enabled: false
        calendar: "*-*-* 03:00:00"
        persistent: true
      retention:
        keep_daily: 7
        keep_weekly: 4
        keep_monthly: 6
        keep_yearly: 0
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import yaml

DEFAULTS = {
    "enabled": False,
    "repository": {
        "type": "local",
        "path": "",
        "host": "",
        "user": "",
        "port": 22,
        "ssh_key_path": "",
        "host_key_check": True,
        "encryption": "repokey",
    },
    "schedule": {
        "enabled": False,
        "calendar": "*-*-* 03:00:00",
        "persistent": True,
    },
    "retention": {
        "keep_daily": 7,
        "keep_weekly": 4,
        "keep_monthly": 6,
        "keep_yearly": 0,
    },
}


class BackupConfigError(ValueError):
    pass


def _load_deploy_yaml(path: Path) -> dict:
    if not path.exists():
        raise BackupConfigError(f"Missing deploy.yaml: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise BackupConfigError("deploy.yaml root must be an object")
    return data


def _to_bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise BackupConfigError(f"{name} must be true/false")


def _non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BackupConfigError(f"{name} must be a non-negative integer")
    return value


def _section(backup: dict, key: str) -> dict:
    value = backup.get(key, {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise BackupConfigError(f"backup.{key} must be an object when provided")
    return value


def load_backup_settings(path: Path) -> dict:
    data = _load_deploy_yaml(path)
    backup = _section(data, "backup")

    enabled = _to_bool("backup.enabled", backup.get("enabled", DEFAULTS["enabled"]))
    repository = _section(backup, "repository")
    schedule = _section(backup, "schedule")
    retention = _section(backup, "retention")

    repo_type = str(repository.get("type", DEFAULTS["repository"]["type"])).strip()
    repo_path = str(repository.get("path", DEFAULTS["repository"]["path"])).strip()
    repo_host = str(repository.get("host", DEFAULTS["repository"]["host"])).strip()
    repo_user = str(repository.get("user", DEFAULTS["repository"]["user"])).strip()
    repo_port = repository.get("port", DEFAULTS["repository"]["port"])
    repo_ssh_key = str(repository.get("ssh_key_path", DEFAULTS["repository"]["ssh_key_path"])).strip()
    repo_host_key_check = _to_bool(
        "backup.repository.host_key_check",
        repository.get("host_key_check", DEFAULTS["repository"]["host_key_check"]),
    )
    repo_encryption = str(repository.get("encryption", DEFAULTS["repository"]["encryption"])).strip()

    if repo_type not in {"local", "sftp"}:
        raise BackupConfigError("backup.repository.type must be 'local' or 'sftp'")
    if repo_encryption not in {"repokey", "none"}:
        raise BackupConfigError("backup.repository.encryption must be 'repokey' or 'none'")

    if enabled:
        if repo_type == "local":
            if not repo_path:
                raise BackupConfigError(
                    "backup.repository.path must be a non-empty string when backup.enabled is true"
                )
            if not repo_path.startswith("/"):
                raise BackupConfigError("backup.repository.path must be an absolute path")
        else:
            if not repo_host:
                raise BackupConfigError("backup.repository.host is required for sftp repositories")
            if not repo_user:
                raise BackupConfigError("backup.repository.user is required for sftp repositories")
            if not repo_path:
                raise BackupConfigError("backup.repository.path is required for sftp repositories")
            if not repo_ssh_key:
                raise BackupConfigError("backup.repository.ssh_key_path is required for sftp repositories")
            key_path = Path(repo_ssh_key).expanduser()
            if not key_path.exists():
                raise BackupConfigError(f"backup.repository.ssh_key_path not found: {key_path}")

    repo_port = _non_negative_int("backup.repository.port", repo_port) or 22

    schedule_enabled = _to_bool(
        "backup.schedule.enabled", schedule.get("enabled", DEFAULTS["schedule"]["enabled"])
    )
    schedule_calendar = str(schedule.get("calendar", DEFAULTS["schedule"]["calendar"]))
    schedule_persistent = _to_bool(
        "backup.schedule.persistent", schedule.get("persistent", DEFAULTS["schedule"]["persistent"])
    )
    if schedule_enabled and (not schedule_calendar.strip()):
        raise BackupConfigError(
            "backup.schedule.calendar must be a non-empty string when backup.schedule.enabled is true"
        )

    retention_values = {
        key: _non_negative_int(f"backup.retention.{key}", retention.get(key, default))
        for key, default in DEFAULTS["retention"].items()
    }

    return {
        "enabled": enabled,
        "repository": {
            "type": repo_type,
            "path": repo_path,
            "host": repo_host,
            "user": repo_user,
            "port": repo_port,
            "ssh_key_path": repo_ssh_key,
            "host_key_check": repo_host_key_check,
            "encryption": repo_encryption,
        },
        "schedule": {
            "enabled": schedule_enabled,
            "calendar": schedule_calendar,
            "persistent": schedule_persistent,
        },
        "retention": retention_values,
    }


def borg_repo_url(settings: dict) -> str:
    """Borg repository location: local path or ssh:// URL (port 22 omitted)."""
    repository = settings["repository"]
    if repository["type"] == "local":
        return repository["path"]
    host = repository["host"]
    port = repository["port"]
    path = repository["path"].lstrip("/")
    if port and port != 22:
        return f"ssh://{repository['user']}@{host}:{port}/{path}"
    return f"ssh://{repository['user']}@{host}/{path}"


def borg_rsh(settings: dict) -> str:
    """BORG_RSH value for sftp repositories; empty for local."""
    repository = settings["repository"]
    if repository["type"] != "sftp":
        return ""
    check = "accept-new" if repository["host_key_check"] else "no"
    parts = ["ssh", "-i", repository["ssh_key_path"], "-o", f"StrictHostKeyChecking={check}"]
    if repository["port"] and repository["port"] != 22:
        parts += ["-p", str(repository["port"])]
    return " ".join(shlex.quote(part) for part in parts)


def _emit_shell(settings: dict) -> str:
    repository = settings["repository"]
    schedule = settings["schedule"]
    retention = settings["retention"]
    values = {
        "BACKUP_ENABLED": "true" if settings["enabled"] else "false",
        "BACKUP_REPO_TYPE": repository["type"],
        "BACKUP_REPO_PATH": repository["path"],
        "BACKUP_REPO_HOST": repository["host"],
        "BACKUP_REPO_USER": repository["user"],
        "BACKUP_REPO_PORT": str(repository["port"]),
        "BACKUP_REPO_SSH_KEY": repository["ssh_key_path"],
        "BACKUP_REPO_HOST_KEY_CHECK": "true" if repository["host_key_check"] else "false",
        "BACKUP_REPO_ENCRYPTION": repository["encryption"],
        "BACKUP_REPO_URL": borg_repo_url(settings),
        "BACKUP_RSH": borg_rsh(settings),
        "BACKUP_SCHEDULE_ENABLED": "true" if schedule["enabled"] else "false",
        "BACKUP_SCHEDULE_CALENDAR": schedule["calendar"],
        "BACKUP_SCHEDULE_PERSISTENT": "true" if schedule["persistent"] else "false",
        "BACKUP_KEEP_DAILY": str(retention["keep_daily"]),
        "BACKUP_KEEP_WEEKLY": str(retention["keep_weekly"]),
        "BACKUP_KEEP_MONTHLY": str(retention["keep_monthly"]),
        "BACKUP_KEEP_YEARLY": str(retention["keep_yearly"]),
    }
    return "\n".join(f"{k}={shlex.quote(v)}" for k, v in values.items())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read/validate shared backup settings from deploy.yaml")
    parser.add_argument("--deploy-yaml", required=True)
    parser.add_argument("--emit-shell", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_backup_settings(Path(args.deploy_yaml))
    if args.emit_shell:
        print(_emit_shell(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
