"""Host filesystem helpers for Easy Deploy apply scripts.

Create FHS data dirs without a root shell: sudo mkdir + chown to the invoking
process (effective uid:gid). Never hardcode a login name such as debian/ubuntu.

Do not trust os.access() — it uses the real uid, while open() uses the
effective uid. A write probe is the source of truth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PARENT_PYTHON_ENV_VARS = (
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "PYTHONHOME",
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON",
    "UV_ACTIVE",
)

_CHOWN_ESCALATE_PREFIXES = ("/var/lib/", "/var/backups/", "/opt/")
_PROBE_NAME = ".easydeploy-write-test"


def isolated_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a nested kit apply/wizard: drop parent uv/venv."""
    env = dict(os.environ if base is None else base)
    for key in PARENT_PYTHON_ENV_VARS:
        env.pop(key, None)
    return env


def service_uid_gid(*, root_default: tuple[int, int] | None = None) -> tuple[int, int]:
    """UID/GID that should own bind-mounted data.

    Non-root: the process that will write files (effective ids). Root: optional
    image convention (e.g. 1000:1000 for OpenCloud) otherwise 0:0.
    """
    if os.geteuid() == 0:
        if root_default is not None:
            return root_default
        return 0, 0
    return os.geteuid(), os.getegid()


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _writer_ids() -> tuple[int, int]:
    return os.geteuid(), os.getegid()


def _privilege_hint(path: Path) -> str:
    uid, gid = _writer_ids()
    return (
        f"Cannot write to {path} (uid {uid} gid {gid}). "
        "Re-run as a login user that can sudo, or set data_dir to a path you own. "
        "Avoid `sudo su` / `sudo -i` — keep the git checkout and .venv owned by your login user."
    )


def _sudo(args: list[str], *, noninteractive: bool = False) -> None:
    if os.geteuid() == 0:
        subprocess.run(args, check=True)
        return
    sudo = shutil.which("sudo")
    if not sudo:
        raise PermissionError(_privilege_hint(Path(args[-1])))
    cmd = [sudo]
    if noninteractive:
        cmd.append("-n")
    cmd.extend(args)
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise PermissionError(_privilege_hint(Path(args[-1]))) from exc


def _probe_write_dir(directory: Path) -> None:
    """Create and delete a probe file using open() (effective uid), not access()."""
    probe = directory / _PROBE_NAME
    fd = os.open(str(probe), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, b"ok")
    finally:
        os.close(fd)
        try:
            probe.unlink()
        except OSError:
            pass


def _take_ownership(path: Path, *, recursive: bool = False) -> None:
    uid, gid = _writer_ids()
    spec = f"{uid}:{gid}"
    if recursive:
        _sudo(["chown", "-R", spec, str(path)])
    else:
        _sudo(["chown", spec, str(path)])


def chown_path(path: Path | str, uid: int, gid: int) -> None:
    """chown path to uid:gid, escalating with sudo for FHS data dirs when needed."""
    target = Path(path)
    try:
        os.chown(target, uid, gid)
        return
    except PermissionError:
        pass
    except OSError:
        return
    resolved = str(target.resolve())
    if not resolved.startswith(_CHOWN_ESCALATE_PREFIXES):
        return
    _sudo(
        ["chown", f"{uid}:{gid}", str(target)],
        noninteractive=not _stdin_is_tty(),
    )


def ensure_writable_directory(path: Path | str) -> Path:
    """mkdir -p path and prove this process can create files in it."""
    target = Path(path).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        _probe_write_dir(target)
        return target
    except PermissionError:
        pass
    except OSError as exc:
        if getattr(exc, "errno", None) != 13:
            raise

    _sudo(["mkdir", "-p", str(target)])
    _take_ownership(target, recursive=True)
    try:
        _probe_write_dir(target)
    except OSError as exc:
        raise PermissionError(_privilege_hint(target)) from exc
    return target


def prepare_writable_file(path: Path | str) -> Path:
    """Make sure this process can open path for writing (create or overwrite)."""
    target = Path(path).expanduser()
    ensure_writable_directory(target.parent)
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o644)
        os.close(fd)
        return target
    except PermissionError:
        pass
    _take_ownership(target, recursive=False)
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o644)
        os.close(fd)
    except OSError as exc:
        raise PermissionError(_privilege_hint(target)) from exc
    return target
