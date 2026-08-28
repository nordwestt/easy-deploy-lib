"""Bash tests for easydeploy-lib/lib/fs.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class EasydeployLibFsTest(unittest.TestCase):
    repo_root = Path(__file__).resolve().parent.parent

    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip())
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def test_default_data_dir_is_var_lib(self):
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "source lib/init.sh && default_data_dir authelia && default_data_dir opencloud",
            ],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            ["/var/lib/authelia", "/var/lib/opencloud"],
        )

    def test_ensure_writable_directory_without_sudo_when_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "svc" / "config"
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source lib/init.sh && ensure_writable_directory "$1"',
                    "bash",
                    str(target),
                ],
                cwd=self.repo_root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(target.is_dir())
            self.assertTrue(os.access(target, os.W_OK))

    def test_ensure_writable_directory_escalates_with_sudo_to_invoking_uid(self):
        if os.geteuid() == 0:
            self.skipTest("sudo escalation is only used when the installer is not root")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            target = root / "var" / "lib" / "authelia"
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
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source lib/init.sh && ensure_writable_directory "$1"',
                    "bash",
                    str(target),
                ],
                cwd=self.repo_root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertTrue(target.is_dir())
            lines = events.read_text().splitlines()
            self.assertTrue(any(line.startswith("sudo:mkdir -p ") for line in lines), msg=lines)
            self.assertTrue(
                any(line.startswith(f"sudo:chown {os.getuid()}:{os.getgid()} ") for line in lines),
                msg=lines,
            )
            self.assertFalse(any("debian" in line.lower() for line in lines))

    def test_clear_parent_python_env(self):
        env = os.environ.copy()
        env["VIRTUAL_ENV"] = "/opt/engine/.venv"
        env["UV_PROJECT"] = "/opt/engine"
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "source lib/init.sh && clear_parent_python_env && "
                'printf "VIRTUAL_ENV=%s\\n" "${VIRTUAL_ENV-unset}" && '
                'printf "UV_PROJECT=%s\\n" "${UV_PROJECT-unset}"',
            ],
            cwd=self.repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("VIRTUAL_ENV=unset", result.stdout)
        self.assertIn("UV_PROJECT=unset", result.stdout)
