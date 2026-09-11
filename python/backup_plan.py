#!/usr/bin/env python3
"""Backup plan loading and payload manifest helpers.

A kit declares WHAT to back up either as a static `backup-plan.yaml` or via a
dynamic `scripts/backup_plan.py` exposing `resolve_plan(project_root) -> dict`
(for plans that depend on deploy.yaml contents). Everything else lives in
easydeploy-lib.

Plan schema — see local://backup-contract.md section 2.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

MANIFEST_FORMAT = 3


class BackupPlanError(ValueError):
    pass


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


def _require_str(plan: dict, key: str) -> str:
    value = plan.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BackupPlanError(f"backup plan: '{key}' must be a non-empty string")
    return value.strip()


def _string_list(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BackupPlanError(f"backup plan: '{key}' must be a list of strings")
    return list(value)


def _path_entry(raw: object, index: int) -> dict:
    if isinstance(raw, str):
        if not raw.strip():
            raise BackupPlanError(f"backup plan: persistent_paths[{index}] must be a non-empty string")
        return {"path": raw.strip(), "as": Path(raw.strip()).name}
    if isinstance(raw, dict):
        path = raw.get("path")
        entry: dict = {}
        if not isinstance(path, str) or not path.strip():
            raise BackupPlanError(f"backup plan: persistent_paths[{index}].path must be a non-empty string")
        entry["path"] = path.strip()
        as_name = raw.get("as")
        if as_name is not None:
            if not isinstance(as_name, str) or not as_name.strip():
                raise BackupPlanError(f"backup plan: persistent_paths[{index}].as must be a non-empty string")
            entry["as"] = as_name.strip()
        else:
            entry["as"] = Path(entry["path"]).name
        for optional in ("mode",):
            if raw.get(optional) is not None:
                entry[optional] = str(raw[optional])
        return entry
    raise BackupPlanError(f"backup plan: persistent_paths[{index}] must be a string or object")


def _load_path_entries(plan: dict) -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(plan.get("persistent_paths") or []):
        entry = _path_entry(raw, index)
        if entry["as"] in seen:
            raise BackupPlanError(f"backup plan: duplicate payload name '{entry['as']}'")
        seen.add(entry["as"])
        entries.append(entry)
    return entries


def _validate_database(entry: object, index: int) -> dict:
    if not isinstance(entry, dict):
        raise BackupPlanError(f"backup plan: databases[{index}] must be an object")
    name = entry.get("name")
    container = entry.get("container")
    if not isinstance(name, str) or not name.strip():
        raise BackupPlanError(f"backup plan: databases[{index}].name must be a non-empty string")
    if not isinstance(container, str) or not container.strip():
        raise BackupPlanError(f"backup plan: databases[{index}].container must be a non-empty string")
    normalized = {
        "name": name.strip(),
        "container": container.strip(),
        "db_name": str(entry.get("db_name") or name).strip(),
        "db_user": str(entry.get("db_user") or name).strip(),
        "exclude_table_data": _string_list(entry.get("exclude_table_data"), "exclude_table_data"),
    }
    normalized["admin_user"] = str(entry.get("admin_user") or normalized["db_user"]).strip()
    normalized["role_password_secret"] = str(entry.get("role_password_secret") or "").strip()
    normalized["admin_password_secret"] = str(entry.get("admin_password_secret") or "").strip()
    if not normalized["role_password_secret"]:
        raise BackupPlanError(
            f"backup plan: databases[{index}].role_password_secret must be a non-empty string"
        )
    if not normalized["admin_password_secret"]:
        normalized["admin_password_secret"] = normalized["role_password_secret"]
    return normalized
def load_plan(project_root: Path) -> dict:
    """Load the kit plan: static backup-plan.yaml or dynamic scripts/backup_plan.py."""
    project_root = Path(project_root)
    dynamic = project_root / "scripts" / "backup_plan.py"
    static = project_root / "backup-plan.yaml"

    if dynamic.exists():
        spec = importlib.util.spec_from_file_location("kit_backup_plan", dynamic)
        if spec is None or spec.loader is None:
            raise BackupPlanError(f"Cannot load plan module: {dynamic}")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(project_root / "easydeploy-lib" / "python"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        if not hasattr(module, "resolve_plan"):
            raise BackupPlanError(f"{dynamic} must define resolve_plan(project_root)")
        plan = module.resolve_plan(project_root)
    elif static.exists():
        plan = _load_yaml(static)
    else:
        raise BackupPlanError(f"No backup plan found: expected {static} or {dynamic}")

    if not isinstance(plan, dict):
        raise BackupPlanError("backup plan root must be an object")

    normalized = {
        "service": _require_str(plan, "service"),
        "archive_prefix": str(plan.get("archive_prefix") or plan["service"]).strip(),
        "timer_name": str(plan.get("timer_name") or f"{project_root.resolve().name}-backup").strip(),
        "state_dir": _require_str(plan, "state_dir"),
        "secrets_file": str(plan.get("secrets_file") or "").strip(),
        "hooks": {
            key: str(value).strip()
            for key, value in (plan.get("hooks") or {}).items()
            if value
        }
        if isinstance(plan.get("hooks") or {}, dict)
        else {},
        "persistent_paths": _load_path_entries(plan),
        "docker_volumes": _string_list(plan.get("docker_volumes"), "docker_volumes"),
        "databases": [
            _validate_database(raw, index) for index, raw in enumerate(plan.get("databases") or [])
        ],
        "legacy_persistent_paths": _string_list(
            plan.get("legacy_persistent_paths"), "legacy_persistent_paths"
        ),
    }
    if "apply" not in normalized["hooks"]:
        raise BackupPlanError("backup plan: hooks.apply is required (restore reconciliation)")
    return normalized


def payload_entries(plan: dict) -> list[dict]:
    """File entries with resolved payload names: [{path, as}]."""
    return [{"path": item["path"], "as": item["as"]} for item in plan["persistent_paths"]]


def database_dump_entries(plan: dict) -> list[dict]:
    return [
        {
            "name": item["name"],
            "path": f"database/{item['name']}.dump",
            "db_name": item["db_name"],
            "db_user": item["db_user"],
            "exclude_table_data": list(item["exclude_table_data"]),
        }
        for item in plan["databases"]
    ]


def write_manifest(
    *,
    manifest_path: Path,
    project_root: Path,
    plan: dict,
    repository_path: str | None,
    encrypted: bool,
) -> None:
    version = "unknown"
    version_file = Path(project_root) / "VERSION"
    if version_file.exists():
        version = version_file.read_text().strip() or version

    manifest = {
        "format": MANIFEST_FORMAT,
        "service": plan["service"],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hostname": socket.gethostname(),
        "version": version,
        "encrypted": encrypted,
        "files": payload_entries(plan),
        "database_dumps": [
            {key: item[key] for key in ("name", "path", "db_user") if key in item}
            for item in database_dump_entries(plan)
        ],
        "volumes": list(plan["docker_volumes"]),
    }
    if repository_path is not None:
        manifest["repository_path"] = repository_path

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def load_manifest(manifest_path: Path) -> dict:
    if not Path(manifest_path).exists():
        raise ValueError(f"Missing manifest: {manifest_path}")
    data = json.loads(Path(manifest_path).read_text())
    if not isinstance(data, dict):
        raise ValueError("manifest root must be an object")
    return data


def normalize_database_dumps(manifest: dict) -> list[dict]:
    """Normalize database dump entries across manifest formats 1/2/3."""
    fmt = manifest.get("format", 1)
    dumps = manifest.get("database_dumps")
    if fmt >= 2 and isinstance(dumps, list):
        normalized = []
        for item in dumps:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                path = str(item.get("path", "")).strip()
                if not name or not path:
                    continue
                entry = {"name": name, "path": path}
                if item.get("db_user"):
                    entry["db_user"] = str(item["db_user"])
                normalized.append(entry)
        if normalized:
            return normalized
    if isinstance(dumps, list):
        normalized = []
        for entry in dumps:
            if isinstance(entry, str):
                normalized.append({"name": "synapse", "path": entry, "db_user": "synapse"})
        if normalized:
            return normalized
    return []


def resolve_payload_files(manifest: dict, payload_root: Path) -> list[tuple[Path, str]]:
    """(payload_path, destination) pairs for format>=3 manifests."""
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    resolved: list[tuple[Path, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        as_name = str(item.get("as", "")).strip()
        path = str(item.get("path", "")).strip()
        if not as_name or not path:
            continue
        resolved.append((Path(payload_root) / "files" / as_name, path))
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup plan and manifest helpers")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--emit-plan-json", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--manifest-path")
    parser.add_argument("--repository-path")
    parser.add_argument("--encrypted", action="store_true")
    parser.add_argument("--read-manifest", action="store_true")
    parser.add_argument("--emit-restore-dumps-json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root)

    if args.emit_plan_json:
        plan = load_plan(project_root)
        print(
            json.dumps(
                {
                    "service": plan["service"],
                    "archive_prefix": plan["archive_prefix"],
                    "timer_name": plan["timer_name"],
                    "state_dir": plan["state_dir"],

                    "secrets_file": plan["secrets_file"],
                    "hooks": plan["hooks"],
                    "persistent_paths": payload_entries(plan),
                    "databases": plan["databases"],
                    "volumes": plan["docker_volumes"],
                    "legacy_persistent_paths": plan["legacy_persistent_paths"],
                }
            )
        )
        return 0

    if args.write_manifest:
        if not args.manifest_path:
            print("--write-manifest requires --manifest-path", file=sys.stderr)
            return 2
        plan = load_plan(project_root)
        write_manifest(
            manifest_path=Path(args.manifest_path),
            project_root=project_root,
            plan=plan,
            repository_path=args.repository_path,
            encrypted=args.encrypted,
        )
        return 0

    if args.read_manifest:
        if not args.manifest_path:
            print("--read-manifest requires --manifest-path", file=sys.stderr)
            return 2
        manifest = load_manifest(args.manifest_path)
        if args.emit_restore_dumps_json:
            print(json.dumps(normalize_database_dumps(manifest)))
            return 0
        print(json.dumps(manifest, indent=2))
        return 0

    print(
        "Nothing to do: pass --emit-plan-json, --write-manifest, or --read-manifest",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
