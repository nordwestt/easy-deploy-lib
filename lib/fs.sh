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

directory_is_writable() {
    local dir="$1"
    [[ -d "$dir" && -w "$dir" && -x "$dir" ]]
}

# Create $1 if needed. When /var/lib (etc.) is not writable, escalate with sudo
# then chown to the current uid:gid so later Python writes do not need root.
ensure_writable_directory() {
    local dir="$1"
    local uid gid parent
    uid="$(id -u)"
    gid="$(id -g)"

    if mkdir -p "$dir" 2>/dev/null && directory_is_writable "$dir"; then
        return 0
    fi

    info "Creating ${dir} with sudo and assigning ownership to uid ${uid} gid ${gid}…"
    run_as_root mkdir -p "$dir"
    run_as_root chown "${uid}:${gid}" "$dir"

    parent="$(dirname "$dir")"
    if [[ "$parent" != "/" && "$parent" != "/var" && "$parent" != "/var/lib" && "$parent" != "/opt" ]]; then
        if [[ -d "$parent" ]] && ! [[ -w "$parent" ]]; then
            run_as_root chown "${uid}:${gid}" "$parent"
        fi
    fi

    if ! directory_is_writable "$dir"; then
        run_as_root chown -R "${uid}:${gid}" "$dir"
    fi

    directory_is_writable "$dir" || die "Cannot write to ${dir}. Use a login user that can sudo, or choose a data directory you own. Avoid 'sudo su' — keep the git checkout owned by your login user."
}
