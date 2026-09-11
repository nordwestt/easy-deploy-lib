"""Tests for easydeploy-lib/python/backup_schedule.py (parameterized units)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import backup_schedule  # noqa: E402


def make_config(tmp_path: Path, backup_enabled: bool = True, schedule_enabled: bool = True) -> Path:
    deploy = tmp_path / "deploy.yaml"
    lines = [
        "services: {}",
        "backup:",
        f"  enabled: {'true' if backup_enabled else 'false'}",
        "  repository:",
        "    path: /var/backups/kit",
    ]
    if schedule_enabled:
        lines += ["  schedule:", "    enabled: true", '    calendar: "*-*-* 04:00:00"']
    deploy.write_text("\n".join(lines) + "\n")
    return deploy


def fake_run(recorder):
    def run(cmd, **kwargs):
        recorder.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_reconcile_installs_parameterized_units(tmp_path):
    deploy = make_config(tmp_path)
    unit_dir = tmp_path / "units"
    runs: list[list[str]] = []

    message = backup_schedule.reconcile(
        tmp_path, deploy, "kit-easy-deploy-backup",
        run_command=fake_run(runs), unit_dir=unit_dir,
    )

    assert "installed" in message or "up to date" in message
    service = unit_dir / "kit-easy-deploy-backup.service"
    timer = unit_dir / "kit-easy-deploy-backup.timer"
    assert service.exists()
    assert timer.exists()
    content = service.read_text()
    assert f"ExecStart=/usr/bin/env bash {tmp_path}/backup.sh" in content
    assert "OnCalendar=*-*-* 04:00:00" in timer.read_text()
    assert "Unit=kit-easy-deploy-backup.service" in timer.read_text()
    assert ["systemctl", "daemon-reload"] in runs
    assert ["systemctl", "enable", "--now", "kit-easy-deploy-backup.timer"] in runs


def test_reconcile_removes_units_when_disabled(tmp_path):
    deploy = make_config(tmp_path, backup_enabled=False, schedule_enabled=False)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "kit-easy-deploy-backup.service").write_text("[Service]\n")
    (unit_dir / "kit-easy-deploy-backup.timer").write_text("[Timer]\n")
    runs: list[list[str]] = []

    message = backup_schedule.reconcile(
        tmp_path, deploy, "kit-easy-deploy-backup",
        run_command=fake_run(runs), unit_dir=unit_dir,
    )

    assert "removed" in message
    assert not (unit_dir / "kit-easy-deploy-backup.service").exists()
    assert not (unit_dir / "kit-easy-deploy-backup.timer").exists()
    assert ["systemctl", "disable", "--now", "kit-easy-deploy-backup.timer"] in runs


def test_schedule_requires_backup_enabled(tmp_path):
    deploy = make_config(tmp_path, backup_enabled=False, schedule_enabled=True)
    with pytest.raises(RuntimeError, match="requires backup.enabled"):
        backup_schedule.reconcile(
            tmp_path, deploy, "kit-easy-deploy-backup", unit_dir=tmp_path / "units"
        )


def test_untouched_when_never_configured(tmp_path):
    deploy = make_config(tmp_path, backup_enabled=False, schedule_enabled=False)
    unit_dir = tmp_path / "units"
    message = backup_schedule.reconcile(
        tmp_path, deploy, "kit-easy-deploy-backup",
        run_command=fake_run([]), unit_dir=unit_dir,
    )
    assert message == "Automatic backup timer not configured."
