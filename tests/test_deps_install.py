"""Integration tests for easydeploy-lib dependency installation."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class EasydeployLibDepsTest(unittest.TestCase):
    repo_root = Path(__file__).resolve().parent.parent

    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body).lstrip())
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _copy_tree(self, src: Path, dest: Path) -> None:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    def test_install_missing_deps_uses_docker_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.log"
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)

            self._copy_tree(self.repo_root / "lib", root / "easydeploy-lib/lib")
            (root / "easydeploy-lib/lib/init.sh").write_text(
                (self.repo_root / "lib/init.sh").read_text()
            )

            scripts = root / "scripts"
            scripts.mkdir()
            self._write_executable(
                scripts / "deps_config.sh",
                """
                easydeploy_required_deps() {
                    printf '%s\\n' docker docker-compose openssl curl python3 borg borgmatic age
                }
                """,
            )
            self._write_executable(
                root / "ensure_dependencies.sh",
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
                source "${SCRIPT_DIR}/easydeploy-lib/lib/init.sh"
                source "${SCRIPT_DIR}/scripts/deps_config.sh"
                ensure_dependencies_installed
                """,
            )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            self._write_executable(
                fake_bin / "dirname",
                "#!/bin/bash\n"
                "path=\"$1\"\n"
                "if [[ \"$path\" == */* ]]; then printf '%s\\n' \"${path%/*}\"; else printf '.\\n'; fi\n",
            )
            self._write_executable(
                fake_bin / "mktemp",
                "#!/bin/bash\nprintf '%s\\n' \"${TMPDIR:-/tmp}/get-docker.test.sh\"\n",
            )
            self._write_executable(
                fake_bin / "sudo",
                "#!/bin/bash\n"
                "if [[ \"${1:-}\" == \"sh\" ]]; then shift; exec /bin/bash \"$@\"; fi\n"
                "exec \"$@\"\n",
            )
            self._write_executable(
                fake_bin / "apt-get",
                "#!/bin/bash\n"
                "echo apt-get:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"install\" ]]; then\n"
                "  for package in \"$@\"; do\n"
                "    case \"$package\" in\n"
                "      borgbackup)\n"
                "        /bin/cat > \"$FAKE_BIN/borg\" <<'EOF'\n"
                "#!/bin/bash\nexit 0\nEOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/borg\"\n"
                "        ;;\n"
                "      borgmatic)\n"
                "        /bin/cat > \"$FAKE_BIN/borgmatic\" <<'EOF'\n"
                "#!/bin/bash\nexit 0\nEOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/borgmatic\"\n"
                "        ;;\n"
                "      age)\n"
                "        /bin/cat > \"$FAKE_BIN/age\" <<'EOF'\n"
                "#!/bin/bash\nexit 0\nEOF\n"
                "        /bin/chmod +x \"$FAKE_BIN/age\"\n"
                "        ;;\n"
                "    esac\n"
                "  done\n"
                "fi\n"
                "exit 0\n",
            )
            self._write_executable(
                fake_bin / "systemctl",
                "#!/bin/bash\n"
                "echo systemctl:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"enable\" && \"${2:-}\" == \"--now\" && \"${3:-}\" == \"docker\" ]]; then\n"
                "  /bin/touch \"$STATE/docker_running\"\n"
                "fi\n",
            )
            self._write_executable(
                fake_bin / "docker",
                "#!/bin/bash\n"
                "if [[ \"${1:-}\" == \"compose\" && \"${2:-}\" == \"version\" ]]; then\n"
                "  [[ -f \"$STATE/docker_compose\" ]] && exit 0; exit 1\n"
                "fi\n"
                "if [[ \"${1:-}\" == \"info\" ]]; then\n"
                "  [[ -f \"$STATE/docker_running\" ]] && exit 0; exit 1\n"
                "fi\n"
                "exit 0\n",
            )
            self._write_executable(fake_bin / "openssl", "#!/bin/bash\nexit 0\n")
            self._write_executable(
                fake_bin / "curl",
                "#!/bin/bash\n"
                "echo curl:$* >> \"$EVENTS\"\n"
                "if [[ \"${1:-}\" == \"-fsSL\" && \"${2:-}\" == \"https://get.docker.com\" && \"${3:-}\" == \"-o\" ]]; then\n"
                "  /bin/cat > \"${4}\" <<'EOF'\n"
                "#!/bin/sh\n"
                "echo docker-script:$* >> \"$EVENTS\"\n"
                "if [ -n \"${STATE:-}\" ]; then /bin/touch \"$STATE/docker_compose\"; fi\n"
                "EOF\n"
                "  /bin/chmod +x \"${4}\"\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
            )
            self._write_executable(fake_bin / "python3", "#!/bin/bash\nexit 0\n")
            self._write_executable(fake_bin / "rm", "#!/bin/bash\nexec /bin/rm \"$@\"\n")

            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            env["EVENTS"] = str(events)
            env["STATE"] = str(state_dir)
            env["FAKE_BIN"] = str(fake_bin)

            result = subprocess.run(
                ["/bin/bash", "ensure_dependencies.sh"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = events.read_text().splitlines()
            events_dump = "\n".join(lines) if lines else "(empty)"
            self.assertTrue(any(line == "apt-get:update" for line in lines), msg=events_dump)
            self.assertTrue(
                any(line == "apt-get:install -y borgbackup borgmatic age" for line in lines),
                msg=events_dump,
            )
            self.assertTrue(any(line.startswith("curl:-fsSL https://get.docker.com -o ") for line in lines))
            self.assertIn("systemctl:enable --now docker", lines)
            self.assertIn("All dependencies satisfied.", result.stdout)

    def _stage_lib(self, root: Path) -> None:
        self._copy_tree(self.repo_root / "lib", root / "easydeploy-lib/lib")
        (root / "easydeploy-lib/lib/init.sh").write_text(
            (self.repo_root / "lib/init.sh").read_text()
        )

    def test_default_required_deps_include_backup_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._stage_lib(root)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source easydeploy-lib/lib/init.sh && required_dependency_keys',
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            keys = [line for line in result.stdout.splitlines() if line.strip()]
            for dep in ("docker", "python3", "borg", "borgmatic", "age"):
                self.assertIn(dep, keys)

    def test_product_hook_adds_to_defaults_instead_of_replacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._stage_lib(root)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "easydeploy_required_deps() { printf '%s\\n' git; }\n"
                    "source easydeploy-lib/lib/init.sh\n"
                    "required_dependency_keys\n",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            keys = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertIn("git", keys)
            self.assertIn("borg", keys)
            self.assertIn("borgmatic", keys)
            self.assertIn("age", keys)
            self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
