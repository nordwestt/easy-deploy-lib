"""Stage a payload and restore it again: the file tree must come back unchanged."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

PLAN = textwrap.dedent(
    """
    def resolve_plan(project_root):
        return {
            "service": "demo",
            "archive_prefix": "demo",
            "timer_name": "demo-backup",
            "state_dir": ".demo",
            "hooks": {"apply": "apply.sh", "stop": "stop.sh"},
            "persistent_paths": [{"path": "data", "as": "data"}],
            "docker_volumes": [],
            "databases": [],
        }
    """
)


class BackupRoundtripTest(unittest.TestCase):
    repo_root = Path(__file__).resolve().parent.parent

    def _bash(self, script: str, *args: str) -> None:
        result = subprocess.run(
            ["/bin/bash", "-c", f"source lib/init.sh && {script}", "bash", *args],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_symlinks_survive_stage_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "scripts").mkdir(parents=True)
            (project / "scripts" / "backup_plan.py").write_text(PLAN)
            for hook in ("apply.sh", "stop.sh"):
                (project / hook).write_text("#!/bin/bash\n")
                (project / hook).chmod(0o755)
            node = project / "data" / "nodes" / "ab" / "cd"
            node.mkdir(parents=True)
            (node / "child").write_text("content")
            parent = project / "data" / "nodes" / "parent"
            parent.mkdir()
            (parent / "settings").symlink_to("../ab/cd")
            (parent / "file").symlink_to("../ab/cd/child")
            (parent / "dangling").symlink_to("../missing")

            stage = Path(tmp) / "stage"
            self._bash('easydeploy_backup_stage_payload "$1" "$2"', str(project), str(stage))
            staged = stage / "payload" / "files" / "data" / "nodes" / "parent"
            self.assertEqual(os.readlink(staged / "settings"), "../ab/cd")

            for link in parent.iterdir():
                link.unlink()
            (parent / "settings").mkdir()

            plan = Path(tmp) / "plan.json"
            self._bash(
                'easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" '
                '--project-root "$1" --emit-plan-json > "$2"',
                str(project),
                str(plan),
            )
            self._bash(
                'easydeploy_backup_restore_payload "$1" "$2" "$3"',
                str(project),
                str(stage / "payload"),
                str(plan),
            )
            self.assertEqual(os.readlink(parent / "settings"), "../ab/cd")
            self.assertEqual(os.readlink(parent / "file"), "../ab/cd/child")
            self.assertEqual(os.readlink(parent / "dangling"), "../missing")
            self.assertEqual((parent / "file").read_text(), "content")


if __name__ == "__main__":
    unittest.main()
