#!/usr/bin/env python3
"""Systemd timer reconciliation for automatic backups (shared)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_config import load_backup_settings  # noqa: E402


def systemd_unit_dir() -> Path:
    return Path(os.environ.get("EASYDEPLOY_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))


def render_service(unit_name: str, project_root: Path, description: str) -> str:
    backup_script = project_root / "backup.sh"
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
            "Wants=network-online.target docker.service",
            "After=network-online.target docker.service",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={project_root}",
            f"ExecStart=/usr/bin/env bash {backup_script}",
            "",
        ]
    )


def render_timer(unit_name: str, calendar: str, persistent: bool) -> str:
    persistent_value = "true" if persistent else "false"
    return "\n".join(
        [
            "[Unit]",
            f"Description=Run {unit_name} backups automatically",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            f"Persistent={persistent_value}",
            f"Unit={unit_name}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True


def systemd_available(run_command=subprocess.run) -> bool:
    if not Path("/run/systemd/system").exists():
        return False
    result = run_command(["systemctl", "--version"], capture_output=True, text=True, check=False)
    return result.returncode == 0


def reconcile(
    project_root: Path,
    deploy_yaml: Path,
    unit_name: str,
    run_command=subprocess.run,
    unit_dir: Path | None = None,
) -> str:
    settings = load_backup_settings(Path(deploy_yaml))
    schedule = settings["schedule"]
    unit_dir = unit_dir or systemd_unit_dir()
    service_path = unit_dir / f"{unit_name}.service"
    timer_path = unit_dir / f"{unit_name}.timer"

    if schedule["enabled"] and not settings["enabled"]:
        raise RuntimeError("backup.schedule.enabled requires backup.enabled=true")

    if schedule["enabled"]:
        if not systemd_available(run_command=run_command):
            raise RuntimeError("Automatic backup scheduling requires systemd on this host")

        try:
            unit_dir.mkdir(parents=True, exist_ok=True)
            service_changed = _write_if_changed(
                service_path,
                render_service(unit_name, project_root, f"{unit_name} automatic backup"),
            )
            timer_changed = _write_if_changed(
                timer_path,
                render_timer(unit_name, schedule["calendar"], schedule["persistent"]),
            )

            run_command(["systemctl", "daemon-reload"], check=True)
            run_command(["systemctl", "enable", "--now", f"{unit_name}.timer"], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Failed to install automatic backup timer: {exc}") from exc

        if service_changed or timer_changed:
            return "Automatic backup timer installed or updated."
        return "Automatic backup timer already up to date."

    if service_path.exists() or timer_path.exists():
        if not systemd_available(run_command=run_command):
            raise RuntimeError("Cannot remove automatic backup timer because systemd is unavailable")

        try:
            run_command(["systemctl", "disable", "--now", f"{unit_name}.timer"], check=False)
            if timer_path.exists():
                timer_path.unlink()
            if service_path.exists():
                service_path.unlink()
            run_command(["systemctl", "daemon-reload"], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Failed to remove automatic backup timer: {exc}") from exc
        return "Automatic backup timer removed."

    return "Automatic backup timer not configured."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile the automatic backup systemd timer")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--deploy-yaml", required=True)
    parser.add_argument("--unit-name", required=True)
    args = parser.parse_args(argv)
    message = reconcile(Path(args.project_root), Path(args.deploy_yaml), args.unit_name)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
