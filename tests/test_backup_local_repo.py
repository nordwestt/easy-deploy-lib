"""Bash tests for easydeploy_backup_prepare_local_repo."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class EasydeployBackupLocalRepoTest(unittest.TestCase):
    repo_root = Path(__file__).resolve().parent.parent

    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip())
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _run(self, script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", "-c", f"source lib/init.sh && {script}", "bash", *args],
            cwd=self.repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_skips_non_local_repositories(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            env = os.environ.copy()
            env["BACKUP_REPO_TYPE"] = "sftp"
            env["BACKUP_REPO_PATH"] = str(target)
            result = self._run("easydeploy_backup_prepare_local_repo", env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertFalse(target.exists())

    def test_creates_writable_local_repo_without_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "backups" / "kit"
            env = os.environ.copy()
            env["BACKUP_REPO_TYPE"] = "local"
            env["BACKUP_REPO_PATH"] = str(target)
            result = self._run("easydeploy_backup_prepare_local_repo", env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(target.is_dir())
            self.assertTrue(os.access(target, os.W_OK))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_escalates_with_sudo_when_mkdir_is_denied(self):
        if os.geteuid() == 0:
            self.skipTest("sudo escalation is only used when the installer is not root")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            target = root / "var" / "backups" / "kanidm"
            self._write_executable(
                fake_bin / "mkdir",
                """\
                #!/bin/bash
                echo mkdir-user:$* >> "$EVENTS"
                exit 1
                """,
            )
            self._write_executable(
                fake_bin / "sudo",
                """\
                #!/bin/bash
                echo sudo:$* >> "$EVENTS"
                if [[ "$1" == "mkdir" ]]; then
                    shift
                    /bin/mkdir "$@"
                elif [[ "$1" == "chown" ]]; then
                    shift
                    /bin/chown "$@"
                fi
                """,
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["EVENTS"] = str(events)
            env["BACKUP_REPO_TYPE"] = "local"
            env["BACKUP_REPO_PATH"] = str(target)
            result = self._run("easydeploy_backup_prepare_local_repo", env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(target.is_dir())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            lines = events.read_text().splitlines()
            self.assertTrue(any(line.startswith("sudo:mkdir -p ") for line in lines), msg=lines)
            self.assertTrue(
                any(line.startswith(f"sudo:chown -R {os.getuid()}:{os.getgid()} ") for line in lines),
                msg=lines,
            )

    def test_requires_repo_path_for_local_repositories(self):
        env = os.environ.copy()
        env["BACKUP_REPO_TYPE"] = "local"
        env.pop("BACKUP_REPO_PATH", None)
        result = self._run("BACKUP_REPO_PATH= easydeploy_backup_prepare_local_repo", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BACKUP_REPO_PATH is required", result.stderr)
