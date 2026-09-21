"""Per-repo update lock: skip restarts when git, config, and images are unchanged.

Each Easy Deploy product stores ``<state_dir>/update.lock`` after a successful
apply. ``update.sh`` compares the current fingerprint and prints
``Nothing to update`` instead of re-applying when nothing changed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

NOTHING_TO_UPDATE = "Nothing to update"
LOCK_VERSION = 1
LOCK_FILENAME = "update.lock"


@dataclass(frozen=True)
class UpdateSpec:
    """How one repo fingerprints itself for skip-if-unchanged updates."""

    project_root: Path
    state_dir: Path
    config_paths: tuple[Path, ...] = ()
    compose_projects: tuple[str, ...] = ()
    extra_containers: tuple[str, ...] = ()
    name_prefixes: tuple[str, ...] = ()

    @property
    def lock_path(self) -> Path:
        return self.state_dir / LOCK_FILENAME


def integration_config_paths(state_dir: Path) -> tuple[Path, ...]:
    """Regular files under ``state_dir/integration/``, sorted."""
    integration = state_dir / "integration"
    if not integration.is_dir():
        return ()
    return tuple(sorted(path for path in integration.rglob("*") if path.is_file()))


def output_reports_noop(output: str) -> bool:
    """True when kit ``update.sh`` stdout ends with the skip line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == NOTHING_TO_UPDATE


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _run_docker(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(["docker", *args], 1, "", "docker not found")


def is_git_checkout(project_root: Path) -> bool:
    result = _run_git(project_root, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_head(project_root: Path) -> str:
    result = _run_git(project_root, "rev-parse", "HEAD")
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_submodules(project_root: Path) -> dict[str, str]:
    result = _run_git(project_root, "submodule", "status", "--recursive")
    if result.returncode != 0:
        return {}
    mapping: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        sha = parts[0].lstrip("+-U")
        path = parts[1]
        if sha and path:
            mapping[path] = sha
    return mapping


def git_is_dirty(project_root: Path) -> bool:
    if not is_git_checkout(project_root):
        return False
    result = _run_git(project_root, "status", "--porcelain")
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def pull_git_repo(project_root: Path, *, verbose: bool = False) -> None:
    """Fast-forward the checkout and refresh submodules. No-op if not a git repo."""
    if not is_git_checkout(project_root):
        if verbose:
            print(f"{project_root.name} is not a git checkout; skipping git pull.")
        return
    if verbose:
        print(f"Pulling {project_root.name}…")
    pull = _run_git(project_root, "pull", "--ff-only")
    if pull.returncode != 0:
        detail = (pull.stderr or pull.stdout or "git pull failed").strip()
        raise RuntimeError(f"Could not git pull {project_root.name}: {detail}")
    if verbose and pull.stdout.strip():
        print(pull.stdout, end="" if pull.stdout.endswith("\n") else "\n")
    extra = ["--quiet"] if not verbose else []
    sub = _run_git(project_root, "submodule", "update", "--init", "--recursive", *extra)
    if sub.returncode != 0:
        detail = (sub.stderr or sub.stdout or "git submodule update failed").strip()
        raise RuntimeError(f"Could not update submodules in {project_root.name}: {detail}")


def config_sha256(paths: Sequence[Path], *, root: Path) -> str:
    """Stable hash of existing config files (missing paths are skipped)."""
    digest = hashlib.sha256()
    root_resolved = root.resolve()
    existing = [path for path in paths if path.is_file()]
    existing.sort(key=lambda path: str(path.resolve()))
    for path in existing:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(root_resolved)
            label = str(rel).replace("\\", "/")
        except ValueError:
            label = str(resolved)
        digest.update(label.encode())
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discover_stack(spec: UpdateSpec) -> list[tuple[str, str]]:
    """Return ``(container_name, image_ref)`` for this kit's running-or-stopped stack."""
    found: dict[str, str] = {}

    def _add(name: str, image: str) -> None:
        name, image = name.strip().lstrip("/"), image.strip()
        if not name or not image or image.startswith("<none>"):
            return
        found[name] = image

    for project in spec.compose_projects:
        listed = _run_docker(
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Names}}\t{{.Image}}",
        )
        if listed.returncode != 0:
            continue
        for line in listed.stdout.splitlines():
            if "\t" not in line:
                continue
            name, image = line.split("\t", 1)
            _add(name, image)

    for name in spec.extra_containers:
        inspected = _run_docker("inspect", "--format", "{{.Name}}\t{{.Config.Image}}", name)
        if inspected.returncode != 0:
            continue
        line = inspected.stdout.strip()
        if "\t" not in line:
            continue
        inspected_name, image = line.split("\t", 1)
        _add(inspected_name, image)

    if spec.name_prefixes:
        listed = _run_docker("ps", "-a", "--format", "{{.Names}}\t{{.Image}}")
        if listed.returncode == 0:
            for line in listed.stdout.splitlines():
                if "\t" not in line:
                    continue
                name, image = line.split("\t", 1)
                stripped = name.strip().lstrip("/")
                if any(stripped.startswith(prefix) for prefix in spec.name_prefixes):
                    _add(stripped, image)

    return sorted(found.items())


def inspect_image_ids(refs: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for ref in refs:
        if not ref:
            continue
        inspected = _run_docker("image", "inspect", "--format", "{{.Id}}", ref)
        if inspected.returncode != 0:
            continue
        image_id = inspected.stdout.strip()
        if image_id:
            mapping[ref] = image_id
    return mapping


def pull_image_refs(refs: Sequence[str], *, quiet: bool = True) -> None:
    for ref in refs:
        if not ref:
            continue
        args = ["pull", "-q", ref] if quiet else ["pull", ref]
        result = _run_docker(*args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "docker pull failed").strip()
            raise RuntimeError(f"docker pull {ref} failed: {detail}")


def container_exists(name: str) -> bool:
    result = _run_docker("inspect", "-f", "{{.Id}}", name)
    return result.returncode == 0 and bool(result.stdout.strip())


def fingerprint_inputs(spec: UpdateSpec) -> dict[str, Any]:
    return {
        "git": git_head(spec.project_root) if is_git_checkout(spec.project_root) else "",
        "submodules": git_submodules(spec.project_root) if is_git_checkout(spec.project_root) else {},
        "config_sha256": config_sha256(spec.config_paths, root=spec.project_root),
    }


def collect_lock_payload(spec: UpdateSpec) -> dict[str, Any]:
    stack = discover_stack(spec)
    refs = sorted({image for _, image in stack if image})
    payload = fingerprint_inputs(spec)
    payload["version"] = LOCK_VERSION
    payload["images"] = inspect_image_ids(refs)
    payload["containers"] = [name for name, _ in stack]
    return payload


def read_lock(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        return None
    return loaded


def write_lock(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(payload, default_flow_style=False, sort_keys=False)
    path.write_text(body)


def record_update_lock(spec: UpdateSpec) -> Path:
    """Write ``<state_dir>/update.lock`` from the current git/config/image state."""
    write_lock(spec.lock_path, collect_lock_payload(spec))
    return spec.lock_path


def should_skip_update(
    spec: UpdateSpec,
    *,
    force: bool = False,
    skip_pull: bool = False,
    verbose: bool = False,
) -> bool:
    """True when git, config, and pulled image IDs all match the lock."""
    if force:
        return False
    saved = read_lock(spec.lock_path)
    if not saved:
        return False
    if git_is_dirty(spec.project_root):
        if verbose:
            print("Working tree has local changes; not skipping update.")
        return False
    current = fingerprint_inputs(spec)
    if current["git"] != str(saved.get("git") or ""):
        return False
    saved_subs = saved.get("submodules") or {}
    if not isinstance(saved_subs, dict) or current["submodules"] != saved_subs:
        return False
    if current["config_sha256"] != str(saved.get("config_sha256") or ""):
        return False
    saved_images = saved.get("images") or {}
    if not isinstance(saved_images, dict) or not saved_images:
        return False
    refs = [str(ref) for ref in saved_images.keys()]
    if not skip_pull:
        try:
            pull_image_refs(refs, quiet=not verbose)
        except RuntimeError:
            return False
    ids = inspect_image_ids(refs)
    if ids != {str(key): str(value) for key, value in saved_images.items()}:
        return False
    expected = [str(name) for name in (saved.get("containers") or [])]
    if not expected:
        return False
    for name in expected:
        if not container_exists(name):
            return False
    return True


def configure_update_logging(*, verbose: bool) -> None:
    if verbose:
        os.environ["EASYDEPLOY_VERBOSE"] = "1"
        os.environ.pop("EASYDEPLOY_QUIET", None)
        os.environ.pop("COMPOSE_PROGRESS", None)
        return
    os.environ["EASYDEPLOY_QUIET"] = "1"
    os.environ["COMPOSE_PROGRESS"] = "quiet"
    os.environ["DOCKER_CLI_HINTS"] = "false"
    os.environ.pop("EASYDEPLOY_VERBOSE", None)


def run_standard_update(
    spec: UpdateSpec,
    *,
    force: bool = False,
    skip_git: bool = False,
    skip_pull: bool = False,
    verbose: bool = False,
    extra_apply_args: Sequence[str] = (),
) -> str:
    """Git-sync (unless skipped), skip or run ``apply.sh``, record the lock.

    Returns ``skipped`` or ``updated``.
    """
    configure_update_logging(verbose=verbose)
    if not skip_git:
        pull_git_repo(spec.project_root, verbose=verbose)
    if should_skip_update(spec, force=force, skip_pull=skip_pull, verbose=verbose):
        print(NOTHING_TO_UPDATE)
        return "skipped"
    apply_sh = spec.project_root / "apply.sh"
    if not apply_sh.is_file():
        raise FileNotFoundError(f"Missing {apply_sh}")
    cmd = ["bash", str(apply_sh), *extra_apply_args]
    result = subprocess.run(cmd, cwd=spec.project_root)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    record_update_lock(spec)
    return "updated"


def add_update_arguments(parser: Any) -> None:
    """Attach the standard ``update.sh`` flags to an argparse parser."""
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show git, Docker, and apply output",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the update lock and always apply",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="Do not git pull (engine already synced this checkout)",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help="Do not pull images; compare local image IDs only",
    )
