#!/usr/bin/env bash
# lib/docker.sh — Docker and Compose helpers (easydeploy-lib)

docker_compose_cmd() {
    if docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null; then
        echo "docker-compose"
    else
        die "Neither 'docker compose' nor 'docker-compose' found. Please install Docker Compose."
    fi
}

docker_usable() {
    command -v docker &>/dev/null || return 1
    docker info &>/dev/null 2>&1
}

compose_usable() {
    docker compose version &>/dev/null 2>&1 || command -v docker-compose &>/dev/null
}

ensure_docker_network() {
    local name="$1"
    if ! docker network inspect "$name" &>/dev/null; then
        info "Creating Docker network: ${name}"
        docker network create "$name" || die "Failed to create Docker network '$name'."
        success "Network '${name}' created."
    else
        info "Docker network '${name}' already exists — skipping."
    fi
}

ensure_docker_volume() {
    local name="$1"
    if ! docker volume inspect "$name" &>/dev/null; then
        info "Creating Docker volume: ${name}"
        docker volume create "$name" || die "Failed to create Docker volume '$name'."
        success "Volume '${name}' created."
    fi
}

install_docker_with_official_script() {
    local prefix_cmd
    command_prefix_for_privileged_install prefix_cmd

    local docker_script
    docker_script="$(mktemp "${TMPDIR:-/tmp}/get-docker.XXXXXX.sh")"

    info "Installing Docker with the official convenience script."
    curl -fsSL https://get.docker.com -o "$docker_script"

    if [[ -n "$prefix_cmd" ]]; then
        "$prefix_cmd" sh "$docker_script" || die "Failed to install Docker"
    else
        sh "$docker_script" || die "Failed to install Docker"
    fi

    rm -f "$docker_script"
}

ensure_docker_daemon_running() {
    if ! command -v docker &>/dev/null; then
        return 0
    fi

    if docker info &>/dev/null 2>&1; then
        return 0
    fi

    warn "Docker is installed but not ready. Attempting to start the daemon."

    local prefix_cmd
    command_prefix_for_privileged_install prefix_cmd

    if command -v systemctl &>/dev/null; then
        if [[ -n "$prefix_cmd" ]]; then
            "$prefix_cmd" systemctl enable --now docker || true
        else
            systemctl enable --now docker || true
        fi
    fi

    if docker info &>/dev/null 2>&1; then
        success "Docker daemon is running."
        return 0
    fi

    die "Docker is installed but the daemon isn't running (or your user cannot access it). Please start Docker and re-run."
}

ensure_docker_and_compose() {
    if docker_usable; then
        success "Docker present ($(docker --version | head -1))"
    else
        if command -v docker &>/dev/null; then
            warn "Docker is installed but the daemon is not reachable — start docker and re-run"
            die "Try: sudo systemctl enable --now docker"
        fi
        install_docker_with_official_script
        if ! docker_usable; then
            if [[ "${EUID}" -ne 0 ]] && ! groups | grep -q '\bdocker\b'; then
                warn "Your user is not in the docker group — log out/in after:"
                warn "  sudo usermod -aG docker $(id -un)"
            fi
            ensure_docker_daemon_running
        else
            success "Docker installed"
        fi
    fi

    if compose_usable; then
        if docker compose version &>/dev/null 2>&1; then
            success "Docker Compose present ($(docker compose version --short 2>/dev/null || docker compose version | head -1))"
        else
            success "docker-compose present"
        fi
    else
        die "Docker Compose v2 is required — reinstall Docker or install the compose plugin"
    fi
}

wait_for_url() {
    local url="$1"
    local label="${2:-service}"
    local max_attempts="${3:-30}"
    local sleep_secs="${4:-5}"

    info "Waiting for ${label} to be ready…"
    local attempt=0
    until curl -fsSL --max-time 5 "$url" &>/dev/null; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            die "Timed out waiting for ${label} at ${url}"
        fi
        echo -ne "    attempt ${attempt}/${max_attempts}…\r"
        sleep "$sleep_secs"
    done
    echo
    success "${label} is up."
}

_docker_info_permission_denied() {
    local out
    out="$(docker info 2>&1)" || true
    grep -qiE 'permission denied|dial unix /var/run/docker.sock' <<<"$out"
}

_docker_group_has_user() {
    local user="$1"
    local members
    members="$(getent group docker 2>/dev/null | cut -d: -f4 || true)"
    [[ -n "$members" ]] || return 1
    case ",${members}," in
        *",${user},"*) return 0 ;;
        *) return 1 ;;
    esac
}

# If Docker is installed but this session cannot use the socket, add the
# invoking user to the docker group and re-exec via `sg docker`.
# Call as: ensure_docker_group_session "$@"  (before consuming argv).
ensure_docker_group_session() {
    local user script bash_bin
    local args=("$@")

    [[ "${EASYDEPLOY_SKIP_DOCKER_GROUP:-}" == "1" ]] && return 0
    [[ "${EUID:-$(id -u)}" -eq 0 ]] && return 0
    command -v docker &>/dev/null || return 0
    docker_usable && return 0
    _docker_info_permission_denied || return 0

    user="$(id -un)"
    if ! getent group docker >/dev/null 2>&1; then
        return 0
    fi

    if ! _docker_group_has_user "$user"; then
        info "Adding ${user} to the docker group so Compose can use the daemon without a root shell."
        run_as_root usermod -aG docker "$user"
    fi

    if docker_usable; then
        return 0
    fi

    if [[ "${EASYDEPLOY_DOCKER_SG:-}" == "1" ]]; then
        warn "Docker group membership is not active in this session. Log out and back in, then re-run."
        return 0
    fi

    command -v sg &>/dev/null || return 0
    bash_bin="$(command -v bash)"
    script="$0"
    info "Activating docker group membership for this session…"
    export EASYDEPLOY_DOCKER_SG=1
    exec sg docker -c "export EASYDEPLOY_DOCKER_SG=1; $(printf '%q ' "$bash_bin" "$script" "${args[@]}")"
}
