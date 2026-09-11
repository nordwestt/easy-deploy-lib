"""Tests for easydeploy-lib/python/backup_plan.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import backup_plan  # noqa: E402

VALID_PLAN = """
service: kanidm
state_dir: .kanidm-easy-deploy
secrets_file: .kanidm-easy-deploy/secrets.yaml
hooks:
  apply: apply.sh
persistent_paths:
  - deploy.yaml
  - path: /var/lib/kanidm
    as: data/kanidm
docker_volumes:
  - caddy_data
databases:
  - name: synapse
    container: matrix_postgres
    admin_user: synapse
    admin_password_secret: POSTGRES_PASSWORD
    role_password_secret: POSTGRES_PASSWORD
    exclude_table_data:
      - e2e_one_time_keys_json
"""


def make_project(tmp_path: Path, plan: str = VALID_PLAN) -> Path:
    (tmp_path / "backup-plan.yaml").write_text(plan)
    return tmp_path


def test_load_plan_normalizes(tmp_path):
    root = make_project(tmp_path)
    plan = backup_plan.load_plan(root)

    assert plan["service"] == "kanidm"
    assert plan["archive_prefix"] == "kanidm"
    assert plan["timer_name"] == f"{tmp_path.resolve().name}-backup"
    assert plan["persistent_paths"] == [
        {"path": "deploy.yaml", "as": "deploy.yaml"},
        {"path": "/var/lib/kanidm", "as": "data/kanidm"},
    ]
    db = plan["databases"][0]
    assert db["container"] == "matrix_postgres"
    assert db["admin_user"] == "synapse"
    assert db["exclude_table_data"] == ["e2e_one_time_keys_json"]


def test_missing_apply_hook_rejected(tmp_path):
    root = make_project(tmp_path, "service: kanidm\nstate_dir: .k\n")
    with pytest.raises(backup_plan.BackupPlanError, match="hooks.apply"):
        backup_plan.load_plan(root)


def test_missing_plan_rejected(tmp_path):
    with pytest.raises(backup_plan.BackupPlanError, match="No backup plan"):
        backup_plan.load_plan(tmp_path)


def test_duplicate_payload_names_rejected(tmp_path):
    root = make_project(
        tmp_path,
        """
service: kanidm
state_dir: .k
hooks:
  apply: apply.sh
persistent_paths:
  - path: /a
    as: same
  - path: /b
    as: same
""",
    )
    with pytest.raises(backup_plan.BackupPlanError, match="duplicate payload name"):
        backup_plan.load_plan(root)


def test_dynamic_plan_module(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "backup_plan.py").write_text(
        """
def resolve_plan(project_root):
    return {
        "service": "matrix",
        "archive_prefix": "MED_Backup",
        "state_dir": ".matrix-easy-deploy",
        "hooks": {"apply": "apply.sh"},
        "persistent_paths": ["deploy.yaml"],
    }
"""
    )
    plan = backup_plan.load_plan(tmp_path)
    assert plan["service"] == "matrix"
    assert plan["archive_prefix"] == "MED_Backup"
    assert plan["databases"] == []


def test_database_requires_role_password_secret(tmp_path):
    root = make_project(
        tmp_path,
        """
service: t
state_dir: .t
hooks:
  apply: apply.sh
databases:
  - name: db1
    container: c1
""",
    )
    with pytest.raises(backup_plan.BackupPlanError, match="role_password_secret"):
        backup_plan.load_plan(root)


def test_write_and_read_manifest_v3(tmp_path):
    root = make_project(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n")
    plan = backup_plan.load_plan(root)
    manifest_path = tmp_path / "payload" / "manifest.json"

    backup_plan.write_manifest(
        manifest_path=manifest_path,
        project_root=root,
        plan=plan,
        repository_path="/var/backups/kit",
        encrypted=False,
    )

    manifest = backup_plan.load_manifest(manifest_path)
    assert manifest["format"] == 3
    assert manifest["service"] == "kanidm"
    assert manifest["version"] == "1.2.3"
    assert manifest["repository_path"] == "/var/backups/kit"
    assert manifest["files"] == [
        {"path": "deploy.yaml", "as": "deploy.yaml"},
        {"path": "/var/lib/kanidm", "as": "data/kanidm"},
    ]
    assert manifest["database_dumps"] == [
        {"name": "synapse", "path": "database/synapse.dump", "db_user": "synapse"}
    ]
    assert manifest["volumes"] == ["caddy_data"]


def test_normalize_database_dumps_v2():
    manifest = {
        "format": 2,
        "database_dumps": [
            {"name": "synapse", "path": "database/synapse.dump", "db_user": "synapse"},
            {"name": "mautrix_whatsapp", "path": "database/mautrix_whatsapp.dump"},
        ],
    }
    dumps = backup_plan.normalize_database_dumps(manifest)
    assert dumps[0] == {"name": "synapse", "path": "database/synapse.dump", "db_user": "synapse"}
    assert dumps[1] == {"name": "mautrix_whatsapp", "path": "database/mautrix_whatsapp.dump"}


def test_normalize_database_dumps_v1_legacy_strings():
    manifest = {"format": 1, "database_dumps": ["database/synapse.dump"]}
    dumps = backup_plan.normalize_database_dumps(manifest)
    assert dumps == [{"name": "synapse", "path": "database/synapse.dump", "db_user": "synapse"}]


def test_normalize_empty_v3():
    assert backup_plan.normalize_database_dumps({"format": 3, "database_dumps": []}) == []


def test_resolve_payload_files(tmp_path):
    manifest = {
        "format": 3,
        "files": [
            {"path": "deploy.yaml", "as": "deploy.yaml"},
            {"path": "/var/lib/kanidm", "as": "data/kanidm"},
            {"bad": "entry"},
        ],
    }
    resolved = backup_plan.resolve_payload_files(manifest, tmp_path)
    assert resolved == [
        (tmp_path / "files" / "deploy.yaml", "deploy.yaml"),
        (tmp_path / "files" / "data" / "kanidm", "/var/lib/kanidm"),
    ]


def test_emit_plan_json_cli(tmp_path, capsys):
    root = make_project(tmp_path)
    exit_code = backup_plan.main(["--project-root", str(root), "--emit-plan-json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "kanidm"
    assert payload["databases"][0]["admin_user"] == "synapse"
    assert payload["legacy_persistent_paths"] == []
