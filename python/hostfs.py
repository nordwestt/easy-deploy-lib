"""Host filesystem helpers for Easy Deploy apply scripts.

Create FHS data dirs without a root shell: sudo mkdir + chown to the invoking
uid:gid. Never hardcode a login name such as debian/ubuntu.
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

_PROTECTED_PARENTS = frozenset({"/", "/var", "/var/lib", "/opt", "/usr", "/etc", "/home"})
_CHOWN_ESCALATE_PREFIXES = ("/var/lib/", "/var/backups/", "/opt/")


def isolated_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a nested kit apply/wizard: drop parent uv/venv."""
    env = dict(os.environ if base is None else base)
    for key in PARENT_PYTHON_ENV_VARS:
        env.pop(key, None)
    return env


def service_uid_gid(*, root_default: tuple[int, int] | None = None) -> tuple[int, int]:
    """UID/GID that should own bind-mounted data.

    Non-root: the invoking user. Root: optional image convention (e.g. 1000:1000
    for OpenCloud) otherwise 0:0.
    """
    if os.geteuid() == 0:
        if root_default is not None:
            return root_default
        return 0, 0
    return os.getuid(), os.getgid()


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _privilege_hint(path: Path) -> str:
    uid = os.getuid()
    gid = os.getgid()
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


def _can_create(path: Path) -> bool:
    cursor = path
    while not cursor.exists() and cursor.parent != cursor:
        cursor = cursor.parent
    try:
        return os.access(cursor, os.W_OK | os.X_OK)
    except OSError:
        return False


def _is_writable_dir(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
    except OSError:
        return False


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
    """mkdir -p path; if /var/lib is not writable, sudo then chown to this user."""
    target = Path(path).expanduser()
    created: list[Path] = []
    cursor = target
    while not cursor.exists() and cursor.parent != cursor:
        created.append(cursor)
        cursor = cursor.parent

    if _can_create(target):
        try:
            target.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            _sudo(["mkdir", "-p", str(target)])
    else:
        _sudo(["mkdir", "-p", str(target)])

    uid, gid = os.getuid(), os.getgid()
    if not _is_writable_dir(target):
        _sudo(["chown", f"{uid}:{gid}", str(target)])

    for ancestor in created:
        if str(ancestor) in _PROTECTED_PARENTS:
            continue
        if ancestor.exists() and not os.access(ancestor, os.W_OK):
            _sudo(["chown", f"{uid}:{gid}", str(ancestor)])

    if not _is_writable_dir(target):
        _sudo(["chown", "-R", f"{uid}:{gid}", str(target)])

    if not _is_writable_dir(target):
        raise PermissionError(_privilege_hint(target))
    return target
