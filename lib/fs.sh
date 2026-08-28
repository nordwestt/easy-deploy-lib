#!/usr/bin/env bash
# lib/fs.sh — host data directories (easydeploy-lib)

# FHS location for a named service. apply.sh creates it with sudo when needed
# and assigns it to the invoking uid:gid (never a hardcoded account name).
default_data_dir() {
    printf '%s\n' "/var/lib/${1}"
}

print_data_dir_hint() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        return 0
    fi
    info "Default data paths are under /var/lib. apply.sh will create them with sudo and assign them to $(id -un) ($(id -u):$(id -g))."
}

_probe_write_dir() {
    local dir="$1"
    local probe="${dir}/.easydeploy-write-test"
    : >"${probe}" && rm -f "${probe}"
}

# Create $1 if needed. Prove we can create files (do not trust [ -w ]).
# When /var/lib is not writable, escalate with sudo then chown -R to the
# invoking uid:gid so later Python writes do not need root.
ensure_writable_directory() {
    local dir="$1"
    local uid gid
    uid="$(id -u)"
    gid="$(id -g)"

    if mkdir -p "$dir" 2>/dev/null && _probe_write_dir "$dir" 2>/dev/null; then
        return 0
    fi

    info "Creating ${dir} with sudo and assigning ownership to uid ${uid} gid ${gid}…"
    run_as_root mkdir -p "$dir"
    run_as_root chown -R "${uid}:${gid}" "$dir"
    _probe_write_dir "$dir" || die "Cannot write to ${dir}. Use a login user that can sudo, or choose a data directory you own. Avoid 'sudo su' — keep the git checkout owned by your login user."
}
