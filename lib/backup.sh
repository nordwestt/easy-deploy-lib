#!/usr/bin/env bash
# lib/backup.sh — shared backup/restore machinery for easydeploy kits.
# Sourced by lib/init.sh. Contract: local://backup-contract.md
#
# A kit provides a plan (backup-plan.yaml or scripts/backup_plan.py) and thin
# backup.sh / restore.sh entrypoints calling the functions below.
# Requires: borg, borgmatic, age, python3 (PyYAML), docker (when the plan uses
# volumes/databases). EASYDEPLOY_BACKUP_PYTHON overrides the python binary.

: "${EASYDEPLOY_LIB:?EASYDEPLOY_LIB must be set before sourcing backup.sh}"
EASYDEPLOY_BACKUP_PYTHON="${EASYDEPLOY_BACKUP_PYTHON:-python3}"

easydeploy_backup_lib_python() {
    printf '%s' "${EASYDEPLOY_LIB}/python"
}

# Runs one of the lib python helpers with the lib import path available.
easydeploy_backup_py() {
    local script="$1"
    shift
    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" "${script}" "$@"
}

# ---------------------------------------------------------------- settings ---

# Print eval-able BACKUP_* exports from a deploy.yaml (python/backup_config.py).
easydeploy_backup_settings_shell() {
    local deploy_yaml="$1"
    "${EASYDEPLOY_BACKUP_PYTHON}" "${EASYDEPLOY_LIB}/python/backup_config.py" \
        --deploy-yaml "${deploy_yaml}" --emit-shell
}

easydeploy_backup_load_settings() {
    local deploy_yaml="$1"
    local exports
    exports="$(easydeploy_backup_settings_shell "${deploy_yaml}")" \
        || die "Invalid backup configuration in ${deploy_yaml}"
}

# ----------------------------------------------------------------- secrets ---

easydeploy_backup_read_secret() {
    local secrets_file="$1"
    local key="$2"

    [[ -n "${secrets_file}" && -f "${secrets_file}" ]] || return 1

    "${EASYDEPLOY_BACKUP_PYTHON}" - "${secrets_file}" "${key}" <<'PY'
import sys
from pathlib import Path

import yaml

data = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
value = data.get(sys.argv[2], "")
if value:
    print(value)
PY
}

# BORG passphrase resolution: explicit env first, then kit secrets.yaml.
easydeploy_backup_passphrase() {
    local secrets_file="$1"

    if [[ -n "${EASYDEPLOY_BACKUP_PASSPHRASE:-}" ]]; then
        printf '%s\n' "${EASYDEPLOY_BACKUP_PASSPHRASE}"
        return 0
    fi
    if [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
        printf '%s\n' "${MED_BACKUP_PASSPHRASE}"
        return 0
    fi
    if [[ -n "${BORG_PASSPHRASE:-}" ]]; then
        printf '%s\n' "${BORG_PASSPHRASE}"
        return 0
    fi
    easydeploy_backup_read_secret "${secrets_file}" "BORG_PASSPHRASE"
}

# Export BORG_PASSPHRASE/BORG_REPO/BORG_RSH for borg & borgmatic commands.
easydeploy_backup_repo_env() {
    local secrets_file="$1"

    local passphrase
    passphrase="$(easydeploy_backup_passphrase "${secrets_file}")"
    if [[ -z "${passphrase}" && "${BACKUP_REPO_ENCRYPTION:-repokey}" != "none" ]]; then
        die "No BORG passphrase found. Set backup.repository.encryption=none or ensure BORG_PASSPHRASE in ${secrets_file}."
    fi
    if [[ -n "${passphrase}" ]]; then
        export BORG_PASSPHRASE="${passphrase}"
    fi

    export BORG_REPO="${BACKUP_REPO_URL:?BACKUP_REPO_URL is required}"
    if [[ -n "${BACKUP_RSH:-}" ]]; then
        export BORG_RSH="${BACKUP_RSH}"
    fi
}

# -------------------------------------------------------------- borgmatic ---

easydeploy_backup_write_borgmatic_config() {
    local config_path="$1"
    local repo_url="$2"
    local working_dir="$3"
    local archive_prefix="$4"

    cat > "${config_path}" <<EOF
source_directories:
  - payload
repositories:
  - path: ${repo_url}
archive_name_format: '${archive_prefix}_{now:%Y-%m-%dT%H:%M:%S}'
keep_daily: ${BACKUP_KEEP_DAILY:-7}
keep_weekly: ${BACKUP_KEEP_WEEKLY:-4}
keep_monthly: ${BACKUP_KEEP_MONTHLY:-6}
keep_yearly: ${BACKUP_KEEP_YEARLY:-0}
working_directory: ${working_dir}
EOF
}

easydeploy_backup_repo_create() {
    local borgmatic_config="$1"

    local encryption_flag
    case "${BACKUP_REPO_ENCRYPTION:-repokey}" in
        none) encryption_flag="none" ;;
        *) encryption_flag="repokey-blake2" ;;
    esac

    info "Ensuring Borg repository exists (encryption: ${encryption_flag})..."
    borgmatic --config "${borgmatic_config}" repo-create --encryption "${encryption_flag}"
}

# ---------------------------------------------------------------- staging ---

easydeploy_backup_resolve_path() {
    local project_root="$1"
    local path="$2"
    case "${path}" in
        /*) printf '%s\n' "${path}" ;;
        *) printf '%s\n' "${project_root}/${path}" ;;
    esac
}

easydeploy_backup_container_running() {
    local container="$1"
    docker ps --format '{{.Names}}' | grep -qx "${container}"
}

easydeploy_backup_export_volume() {
    local volume="$1"
    local target_dir="$2"

    if ! docker volume inspect "${volume}" &>/dev/null; then
        warn "Docker volume '${volume}' not found, skipping."
        return 0
    fi

    info "Exporting Docker volume '${volume}'..."
    docker run --rm \
        -v "${volume}:/source:ro" \
        -v "${target_dir}/backup" \
        alpine:3 sh -c "mkdir -p /backup && cd /source && tar -cf /backup/${volume}.tar ."
}

# Print "name<TAB>value" hook lines from a plan json file.
easydeploy_backup_plan_hooks() {
    local plan_json="$1"
    "${EASYDEPLOY_BACKUP_PYTHON}" -c \
        'import json,sys
plan = json.load(open(sys.argv[1]))
for key, value in sorted(plan.get("hooks", {}).items()):
    print(f"{key}\t{value}")' "${plan_json}"
}

easydeploy_backup_run_hook() {
    local project_root="$1"
    local hook="$2"

    [[ -n "${hook}" ]] || return 0

    local hook_path
    hook_path="$(easydeploy_backup_resolve_path "${project_root}" "${hook}")"
    [[ -f "${hook_path}" ]] || die "Hook script not found: ${hook_path}"
    (cd "${project_root}" && bash "${hook_path}")
}

# pg_dump every plan database into payload/database/. Driver in python for
# clean JSON access; docker exec + PGPASSWORD matches kit-verified semantics.
easydeploy_backup_dump_databases() {
    local project_root="$1"
    local payload_dir="$2"
    local plan_json="$3"

    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" - "${project_root}" "${payload_dir}" "${plan_json}" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

project_root, payload_dir, plan_json_path = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.loads(Path(plan_json_path).read_text())
databases = plan.get("databases", [])
if not databases:
    raise SystemExit(0)

def secret(key: str) -> str:
    if not key:
        return ""
    value = os.environ.get(key, "")
    if value:
        return value
    secrets_file = plan.get("secrets_file", "")
    if secrets_file:
        import yaml
        path = Path(secrets_file)
        if not path.is_absolute():
            path = Path(project_root) / secrets_file
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            return str(data.get(key) or "")
    return ""

running = subprocess.run(
    ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True
).stdout.splitlines()

for item in databases:
    container = item["container"]
    if container not in running:
        print(f"ERROR: container '{container}' is not running; start the stack before a live backup.", file=sys.stderr)
        raise SystemExit(1)

    admin_password = secret(item["admin_password_secret"])
    if not admin_password:
        print(f"ERROR: {item['admin_password_secret']} not available for '{item['name']}' dump.", file=sys.stderr)
        raise SystemExit(1)

    dump_file = Path(payload_dir) / "database" / f"{item['name']}.dump"
    cmd = ["docker", "exec", "-e", f"PGPASSWORD={admin_password}", container,
           "pg_dump", "-U", item["admin_user"], "-d", item["db_name"], "-Fc"]
    for table in item.get("exclude_table_data", []):
        cmd.append(f"--exclude-table-data={table}")
    with open(dump_file, "wb") as handle:
        subprocess.run(cmd, check=True, stdout=handle)
    print(f"dumped:{item['db_name']}")
PY
}

easydeploy_backup_write_manifest() {
    local project_root="$1"
    local payload_dir="$2"
    local repository_path="${3:-}"
    local encrypted="${4:-false}"

    local -a args=(--write-manifest --manifest-path "${payload_dir}/manifest.json" --project-root "${project_root}")
    if [[ -n "${repository_path}" ]]; then
        args+=(--repository-path "${repository_path}")
    fi
    if [[ "${encrypted}" == "true" ]]; then
        args+=(--encrypted)
    fi
    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" "${args[@]}"
}

# Stage payload/ (files + databases + volumes + manifest) into staging_current.
easydeploy_backup_stage_payload() {
    local project_root="$1"
    local staging_current="$2"
    local repository_path="${3:-}"
    local encrypted="${4:-false}"

    local payload_dir="${staging_current}/payload"
    rm -rf "${staging_current}"
    mkdir -p "${payload_dir}/files" "${payload_dir}/docker-volumes" "${payload_dir}/database"

    local plan_json
    plan_json="$(mktemp)"
    trap 'rm -f "${plan_json:-}"' RETURN

    easydeploy_backup_py "${EASYDEPLOY_LIB}/python/backup_plan.py" \
        --project-root "${project_root}" --emit-plan-json > "${plan_json}"

    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" - "${project_root}" "${payload_dir}" "${plan_json}" <<'PY'
import json
import shutil
import subprocess
import sys
from pathlib import Path

project_root, payload_dir, plan_json_path = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.loads(Path(plan_json_path).read_text())
root = Path(project_root)

for entry in plan["persistent_paths"]:
    src = Path(entry["path"]) if entry["path"].startswith("/") else root / entry["path"]
    dest = Path(payload_dir) / "files" / entry["as"]
    if not src.exists():
        print(f"WARN: backup path missing, skipping: {entry['path']}", file=sys.stderr)
        continue
    if src.is_dir():
        # The staging root may live inside a staged dir (state_dir/backup/...) —
        # never recurse into it.
        shutil.copytree(
            src,
            dest,
            ignore=shutil.ignore_patterns("backup"),
            dirs_exist_ok=True,
        )
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    print(f"staged:{entry['as']}")
PY

    easydeploy_backup_dump_databases "${project_root}" "${payload_dir}" "${plan_json}"

    local volume
    while IFS= read -r volume; do
        [[ -n "${volume}" ]] || continue
        easydeploy_backup_export_volume "${volume}" "${payload_dir}/docker-volumes"
    done < <("${EASYDEPLOY_BACKUP_PYTHON}" - "${plan_json}" <<'PY'
import json
import sys

for volume in json.load(open(sys.argv[1])).get("volumes", []):
    print(volume)
PY
)

    easydeploy_backup_write_manifest "${project_root}" "${payload_dir}" "${repository_path}" "${encrypted}"
}

# ---------------------------------------------------------------- archives ---

easydeploy_backup_list_archives() {
    local repo_url="$1"
    borg list --format '{archive}{NL}' "${repo_url}"
}

easydeploy_backup_resolve_archive() {
    local repo_url="$1"
    local requested="$2"
    local entries matches

    entries="$(borg list "${repo_url}")"

    matches="$(printf '%s\n' "${entries}" | awk -v requested="${requested}" '
        {
            archive=$1
            archive_id=$NF
            gsub(/^\[/, "", archive_id)
            gsub(/\]$/, "", archive_id)
            if (archive == requested || index(archive_id, requested) == 1) {
                print archive
            }
        }
    ')"

    if [[ -z "${matches}" ]]; then
        die "Archive '${requested}' does not exist. Use --list and pass the full archive name or a unique ID prefix."
    fi

    if [[ "$(printf '%s\n' "${matches}" | wc -l)" -gt 1 ]]; then
        error "Archive reference '${requested}' is ambiguous. Matching archive names:"
        printf '%s\n' "${matches}" >&2
        exit 1
    fi

    printf '%s\n' "${matches}"
}

# ------------------------------------------------------- portable archives ---

easydeploy_backup_is_encrypted_path() {
    case "$1" in
        *.age|*.enc) return 0 ;;
    esac
    return 1
}

easydeploy_backup_is_openssl_encrypted() {
    local path="$1"
    [[ "$(head -c 8 "${path}" 2>/dev/null || true)" == "Salted__" ]]
}

# Encrypt a tar stream into output_path. Non-interactive when a passphrase env
# is set (EASYDEPLOY_BACKUP_PASSPHRASE / MED_BACKUP_PASSPHRASE); else age prompt.
easydeploy_backup_encrypt_stream() {
    local output_path="$1"

    local passphrase_env=""
    if [[ -n "${EASYDEPLOY_BACKUP_PASSPHRASE:-}" ]]; then
        passphrase_env="EASYDEPLOY_BACKUP_PASSPHRASE"
    elif [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
        passphrase_env="MED_BACKUP_PASSPHRASE"
    fi

    if [[ -n "${passphrase_env}" ]]; then
        command -v openssl &>/dev/null || die "Required command not found: openssl"
        gzip -c | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "env:${passphrase_env}" -out "${output_path}"
        return 0
    fi

    command -v age &>/dev/null || die "Required command not found: age"
    if [[ ! -t 0 && ! -t 2 ]]; then
        die "Encryption requires an interactive terminal or EASYDEPLOY_BACKUP_PASSPHRASE."
    fi
    gzip -c | age -e -p -o "${output_path}" -
}

easydeploy_backup_decrypt_stream() {
    local input_path="$1"

    if easydeploy_backup_is_openssl_encrypted "${input_path}"; then
        command -v openssl &>/dev/null || die "Required command not found: openssl"
        if [[ -n "${PASSPHRASE_FILE:-}" ]]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:${PASSPHRASE_FILE}" -in "${input_path}" | gzip -dc
        elif [[ -n "${EASYDEPLOY_BACKUP_PASSPHRASE:-}" ]]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -pass env:EASYDEPLOY_BACKUP_PASSPHRASE -in "${input_path}" | gzip -dc
        elif [[ -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
            openssl enc -d -aes-256-cbc -pbkdf2 -pass env:MED_BACKUP_PASSPHRASE -in "${input_path}" | gzip -dc
        else
            openssl enc -d -aes-256-cbc -pbkdf2 -pass stdin -in "${input_path}" | gzip -dc
        fi
        return 0
    fi

    command -v age &>/dev/null || die "Required command not found: age"
    if [[ -n "${PASSPHRASE_FILE:-}" ]]; then
        die "--passphrase-file is not supported for age-encrypted archives; use EASYDEPLOY_BACKUP_PASSPHRASE or decrypt interactively."
    fi
    if [[ -n "${EASYDEPLOY_BACKUP_PASSPHRASE:-}" || -n "${MED_BACKUP_PASSPHRASE:-}" ]]; then
        die "Passphrase env vars cannot decrypt age-encrypted archives; enter the passphrase interactively."
    fi
    age -d -o - "${input_path}" | gzip -dc
}

easydeploy_backup_export_portable() {
    local export_path="$1"
    local staging_current="$2"
    local encrypted="${3:-false}"

    local export_dir
    export_dir="$(dirname "${export_path}")"
    mkdir -p "${export_dir}"

    info "Writing portable archive to ${export_path}..."
    if [[ "${encrypted}" == "true" ]]; then
        (cd "${staging_current}" && tar -cf - payload) | easydeploy_backup_encrypt_stream "${export_path}"
    else
        tar -C "${staging_current}" -cf - payload | gzip -c > "${export_path}"
    fi

    success "Portable archive written to ${export_path}"
}

easydeploy_backup_export_from_archive() {
    local repo_url="$1"
    local archive_name="$2"
    local export_path="$3"
    local encrypted="${4:-false}"

    info "Exporting Borg archive '${archive_name}' to portable format..."
    if [[ "${encrypted}" == "true" ]]; then
        borg export-tar "${repo_url}::${archive_name}" - | easydeploy_backup_encrypt_stream "${export_path}"
    else
        borg export-tar "${repo_url}::${archive_name}" - | gzip -c > "${export_path}"
    fi

    success "Portable archive written to ${export_path}"
}

easydeploy_backup_extract_portable() {
    local file_path="$1"
    local extract_dir="$2"

    [[ -f "${file_path}" ]] || die "Portable archive not found: ${file_path}"

    mkdir -p "${extract_dir}"

    if easydeploy_backup_is_encrypted_path "${file_path}" || easydeploy_backup_is_openssl_encrypted "${file_path}"; then
        info "Decrypting and extracting portable archive..."
        easydeploy_backup_decrypt_stream "${file_path}" | tar -xf - -C "${extract_dir}"
    else
        info "Extracting portable archive..."
        gzip -dc "${file_path}" | tar -xf - -C "${extract_dir}"
    fi
}

# ---------------------------------------------------------------- postgres ---

easydeploy_backup_ensure_postgres_prerequisite() {
    local project_root="$1"
    local plan_json="$2"

    local postgres_hook
    postgres_hook="$(
        "${EASYDEPLOY_BACKUP_PYTHON}" -c \
            'import json,sys
print(json.load(open(sys.argv[1])).get("hooks", {}).get("postgres_start", ""))' "${plan_json}"
    )"

    if [[ -n "${postgres_hook}" ]]; then
        info "Starting PostgreSQL prerequisite..."
        easydeploy_backup_run_hook "${project_root}" "${postgres_hook}"
        return 0
    fi

    local container
    while IFS= read -r container; do
        [[ -n "${container}" ]] || continue
        if ! easydeploy_backup_container_running "${container}"; then
            die "Container '${container}' is not running and the plan defines no postgres_start hook."
        fi
    done < <("${EASYDEPLOY_BACKUP_PYTHON}" - "${plan_json}" <<'PY'
import json
import sys

for item in json.load(open(sys.argv[1])).get("databases", []):
    print(item["container"])
PY
)
}

# Copy one restore phase back into place. phase: config | data.
# Directory entries merge into the existing tree so a restore never deletes the
# kit's own backup working dir (staging/restore live under <state_dir>/backup).
easydeploy_backup_restore_copy_phase() {
    local phase="$1"
    local project_root="$2"
    local payload_root="$3"
    local plan_json="$4"

    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" - "${phase}" "${project_root}" "${payload_root}" "${plan_json}" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("EASYDEPLOY_LIB_PYTHON", ""))
from backup_plan import resolve_payload_files  # noqa: E402

phase, project_root, payload_root, plan_json_path = sys.argv[1:5]
root = Path(project_root)
plan = json.loads(Path(plan_json_path).read_text())
manifest = json.loads((Path(payload_root) / "manifest.json").read_text())
fmt = int(manifest.get("format", 1))

def is_config(rel_path: str) -> bool:
    state_dir = plan["state_dir"].strip("/")
    rel = rel_path.strip("/")
    return rel == "deploy.yaml" or rel == state_dir or rel.startswith(state_dir + "/")

if fmt >= 3:
    entries = [(src, rel) for src, rel in resolve_payload_files(manifest, payload_root)]
else:
    legacy = plan.get("legacy_persistent_paths") or []
    entries = [(Path(payload_root) / rel, rel) for rel in legacy]

def rm(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()

for src, rel in entries:
    if (phase == "config") != is_config(rel):
        continue
    if not src.exists():
        print(f"WARN: payload entry missing: {rel}", file=sys.stderr)
        continue
    dest = Path(rel) if rel.startswith("/") else root / rel
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in dest.iterdir():
            if child.name == "backup":
                continue
            rm(child)
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        rm(dest)
        shutil.copy2(src, dest)
    print(f"restored:{rel}")
PY
}

# pg_restore every manifest dump using the plan entry for credentials/roles.
easydeploy_backup_restore_databases() {
    local project_root="$1"
    local payload_root="$2"
    local plan_json="$3"

    local manifest_path="${payload_root}/manifest.json"
    [[ -f "${manifest_path}" ]] || return 0

    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" - "${project_root}" "${payload_root}" "${plan_json}" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("EASYDEPLOY_LIB_PYTHON", ""))
from backup_plan import normalize_database_dumps  # noqa: E402

project_root, payload_root, plan_json_path = sys.argv[1], sys.argv[2], sys.argv[3]
plan = json.loads(Path(plan_json_path).read_text())
manifest = json.loads((Path(payload_root) / "manifest.json").read_text())
dumps = normalize_database_dumps(manifest)
if not dumps:
    raise SystemExit(0)

plan_dbs = {item["name"]: item for item in plan.get("databases", [])}

def secret(key: str) -> str:
    if not key:
        return ""
    value = os.environ.get(key, "")
    if value:
        return value
    secrets_file = plan.get("secrets_file", "")
    if secrets_file:
        import yaml
        path = Path(secrets_file)
        if not path.is_absolute():
            path = Path(project_root) / secrets_file
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            return str(data.get(key) or "")
    return ""

def admin_psql(container: str, admin_user: str, admin_password: str, *args: str) -> None:
    subprocess.run(
        ["docker", "exec", "-e", f"PGPASSWORD={admin_password}", container,
         "psql", "-U", admin_user, "-v", "ON_ERROR_STOP=1", *args],
        check=True,
    )

for dump in dumps:
    name = dump["name"]
    entry = plan_dbs.get(name)
    if entry is None:
        print(f"WARN: no plan entry for database dump '{name}' — skipping", file=sys.stderr)
        continue

    container = entry["container"]
    admin_password = secret(entry["admin_password_secret"])
    role_password = secret(entry["role_password_secret"])
    if not admin_password or not role_password:
        print(f"ERROR: database credentials for '{name}' not available.", file=sys.stderr)
        raise SystemExit(1)

    dump_file = Path(payload_root) / dump["path"]
    if not dump_file.exists():
        print(f"WARN: dump file missing: {dump['path']}", file=sys.stderr)
        continue

    print(f"restoring database '{name}'...")
    admin_psql(
        container, entry["admin_user"], admin_password, "-c",
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '%s') THEN "
        "CREATE ROLE %s LOGIN PASSWORD '%s'; "
        "ELSE ALTER ROLE %s WITH PASSWORD '%s'; END IF; END $$;"
        % (entry["db_user"], entry["db_user"], role_password, entry["db_user"], role_password),
    )
    admin_psql(container, entry["admin_user"], admin_password, "-d", "postgres",
               "-c", f"DROP DATABASE IF EXISTS {entry['db_name']} WITH (FORCE);")
    admin_psql(container, entry["admin_user"], admin_password, "-d", "postgres",
               "-c", f"CREATE DATABASE {entry['db_name']} OWNER {entry['db_user']} "
                     f"ENCODING 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE template0;")
    subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={role_password}", container,
         "pg_restore", "-U", entry["db_user"], "-d", entry["db_name"],
         "--no-owner", "--no-privileges"],
        stdin=open(dump_file, "rb"),
        check=True,
    )
    print(f"restored:{name}")
PY
}

# ----------------------------------------------------------------- restore ---

easydeploy_backup_warn_version_mismatch() {
    local payload_root="$1"
    local project_root="$2"

    local manifest_path="${payload_root}/manifest.json"
    [[ -f "${manifest_path}" ]] || return 0

    local warning
    warning="$("${EASYDEPLOY_BACKUP_PYTHON}" - "${manifest_path}" "${project_root}/VERSION" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
version_file = Path(sys.argv[2])
backup_version = str(manifest.get("version", "unknown")).strip()
current_version = version_file.read_text().strip() if version_file.exists() else "unknown"

def major_minor(version: str):
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None

backup_mm = major_minor(backup_version)
current_mm = major_minor(current_version)
if backup_mm and current_mm and backup_mm != current_mm:
    print(f"backup version {backup_version} differs from toolkit version {current_version}")
PY
)"

    if [[ -n "${warning}" ]]; then
        warn "${warning}"
    fi
}

# Import docker volumes listed in the manifest (fallback: plan volumes).
easydeploy_backup_import_volumes() {
    local payload_root="$1"
    local plan_json="$2"

    EASYDEPLOY_LIB_PYTHON="$(easydeploy_backup_lib_python)" \
        "${EASYDEPLOY_BACKUP_PYTHON}" - "${payload_root}" "${plan_json}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

payload_root, plan_json_path = sys.argv[1], sys.argv[2]
plan = json.loads(Path(plan_json_path).read_text())
manifest_path = Path(payload_root) / "manifest.json"
volumes = plan["volumes"]
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
    if isinstance(manifest.get("volumes"), list) and manifest["volumes"]:
        volumes = manifest["volumes"]

volumes_dir = Path(payload_root) / "docker-volumes"
for volume in volumes:
    archive = volumes_dir / f"{volume}.tar"
    if not archive.exists():
        print(f"WARN: volume archive missing: {archive}", file=sys.stderr)
        continue
    print(f"importing volume '{volume}'...")
    subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=False)
    subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{volume}:/target",
         "-v", f"{volumes_dir}:/backup:ro",
         "alpine:3",
         "sh", "-c",
         "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; "
         "cd /target && tar -xf /backup/" + f"{volume}.tar"],
        check=True,
    )
PY
}

# Restore an extracted payload/ directory into the project. Handles manifest
# format 3 (files/ layout) and legacy formats 1/2 (flat repo-relative layout).
# Order: config files -> apply -> volumes -> data files -> databases -> apply.
easydeploy_backup_restore_payload() {
    local project_root="$1"
    local payload_root="$2"
    local plan_json="$3"

    [[ -d "${payload_root}" ]] || die "Payload directory not found: ${payload_root}"
    [[ -f "${payload_root}/manifest.json" ]] || die "Payload does not contain manifest.json"

    easydeploy_backup_warn_version_mismatch "${payload_root}" "${project_root}"

    local apply_hook
    apply_hook="$("${EASYDEPLOY_BACKUP_PYTHON}" -c \
        'import json,sys
print(json.load(open(sys.argv[1])).get("hooks", {}).get("apply", ""))' "${plan_json}")"

    info "Restoring configuration files..."
    easydeploy_backup_restore_copy_phase config "${project_root}" "${payload_root}" "${plan_json}"

    if [[ -n "${apply_hook}" ]]; then
        info "Regenerating runtime artifacts from restored deploy/state..."
        easydeploy_backup_run_hook "${project_root}" "${apply_hook}"
    fi

    easydeploy_backup_import_volumes "${payload_root}" "${plan_json}"

    info "Restoring data files..."
    easydeploy_backup_restore_copy_phase data "${project_root}" "${payload_root}" "${plan_json}"

    easydeploy_backup_ensure_postgres_prerequisite "${project_root}" "${plan_json}"
    easydeploy_backup_restore_databases "${project_root}" "${payload_root}" "${plan_json}"

    if [[ -n "${apply_hook}" ]]; then
        info "Final reconciliation after payload restore..."
        easydeploy_backup_run_hook "${project_root}" "${apply_hook}"
    fi
}
