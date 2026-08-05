#!/usr/bin/env bash
# lib/deps.sh — dependency check and install framework (easydeploy-lib)

required_dependency_keys() {
    if declare -F easydeploy_required_deps &>/dev/null; then
        easydeploy_required_deps
        return 0
    fi
    printf '%s\n' docker docker-compose openssl curl python3
}

is_dependency_missing() {
    local dep="$1"

    case "$dep" in
        docker)
            ! command -v docker &>/dev/null
            ;;
        docker-compose)
            ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null
            ;;
        openssl|curl|python3|git)
            ! command -v "$dep" &>/dev/null
            ;;
        borg)
            ! command -v borg &>/dev/null
            ;;
        borgmatic)
            ! command -v borgmatic &>/dev/null
            ;;
        age)
            ! command -v age &>/dev/null
            ;;
        *)
            die "Unknown dependency key: ${dep}"
            ;;
    esac
}

collect_missing_dependencies() {
    local output_var="$1"
    local missing=()
    local dep

    while IFS= read -r dep; do
        [[ -z "$dep" ]] && continue
        if is_dependency_missing "$dep"; then
            missing+=("$dep")
        fi
    done < <(required_dependency_keys)

    local joined_missing=""
    if [[ ${#missing[@]} -gt 0 ]]; then
        local old_ifs="$IFS"
        IFS=' '
        joined_missing="${missing[*]}"
        IFS="$old_ifs"
    fi

    printf -v "$output_var" '%s' "$joined_missing"
}

join_words() {
    local output_var="$1"
    shift

    local joined=""
    if [[ $# -gt 0 ]]; then
        local old_ifs="$IFS"
        IFS=' '
        joined="$*"
        IFS="$old_ifs"
    fi

    printf -v "$output_var" '%s' "$joined"
}

dependency_packages_for_manager() {
    local manager="$1"
    local dep="$2"

    if declare -F easydeploy_dependency_packages_for_manager &>/dev/null; then
        local custom=""
        if custom="$(easydeploy_dependency_packages_for_manager "$manager" "$dep")"; then
            echo "$custom"
            return 0
        fi
    fi

    case "$manager:$dep" in
        apt-get:openssl) echo "openssl" ;;
        apt-get:curl) echo "curl" ;;
        apt-get:python3) echo "python3" ;;
        apt-get:git) echo "git" ;;
        apt-get:borg) echo "borgbackup" ;;
        apt-get:borgmatic) echo "borgmatic" ;;
        apt-get:age) echo "age" ;;
        dnf:openssl) echo "openssl" ;;
        dnf:curl) echo "curl" ;;
        dnf:python3) echo "python3" ;;
        dnf:git) echo "git" ;;
        dnf:borg) echo "borgbackup" ;;
        dnf:borgmatic) echo "borgmatic" ;;
        dnf:age) echo "age" ;;
        pacman:openssl) echo "openssl" ;;
        pacman:curl) echo "curl" ;;
        pacman:python3) echo "python" ;;
        pacman:git) echo "git" ;;
        pacman:borg) echo "borg" ;;
        pacman:borgmatic) echo "borgmatic" ;;
        pacman:age) echo "age" ;;
        *) die "No package mapping for ${dep} via ${manager}" ;;
    esac
}

docker_install_required() {
    is_dependency_missing "docker" || is_dependency_missing "docker-compose"
}

install_missing_dependencies() {
    local manager="$1"
    shift

    local missing=("$@")
    if [[ ${#missing[@]} -eq 0 ]]; then
        success "All required dependencies are already installed."
        return 0
    fi

    local prefix_cmd
    command_prefix_for_privileged_install prefix_cmd

    local packages=()
    local -A seen_packages=()
    local install_docker="false"
    local dep
    local package
    for dep in "${missing[@]}"; do
        if [[ "$dep" == "docker" || "$dep" == "docker-compose" ]]; then
            install_docker="true"
            continue
        fi

        package="$(dependency_packages_for_manager "$manager" "$dep")"
        if [[ -z "${seen_packages[$package]:-}" ]]; then
            packages+=("$package")
            seen_packages["$package"]=1
        fi
    done

    if [[ ${#packages[@]} -gt 0 ]]; then
        info "Installing missing dependencies with ${manager}: ${packages[*]}"
        install_packages_with_manager "$manager" "$prefix_cmd" "${packages[@]}" \
            || die "Failed to install packages with ${manager}"
    fi

    if [[ "$install_docker" == "true" ]]; then
        install_docker_with_official_script
    fi
}

ensure_dependencies_installed() {
    info "Ensuring required dependencies are installed…"

    local missing_text
    collect_missing_dependencies missing_text

    local missing=()
    if [[ -n "$missing_text" ]]; then
        IFS=' ' read -r -a missing <<< "$missing_text"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        local manager
        manager="$(detect_supported_package_manager)" || die "No supported package manager found. Install docker, docker compose, openssl, curl, and python3 manually."
        install_missing_dependencies "$manager" "${missing[@]}"
    else
        success "All required packages are already present."
    fi

    ensure_docker_daemon_running
    check_dependencies
}

check_dependencies() {
    info "Checking dependencies…"

    local missing=()

    if is_dependency_missing "docker"; then
        missing+=("docker")
    elif ! docker info &>/dev/null 2>&1; then
        die "Docker is installed but the daemon isn't running (or you need sudo). Please start Docker and re-run."
    fi

    local dep
    while IFS= read -r dep; do
        [[ "$dep" == "docker" ]] && continue
        if is_dependency_missing "$dep"; then
            missing+=("$dep")
        fi
    done < <(required_dependency_keys)

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "The following required tools are missing:"
        for dep in "${missing[@]}"; do
            echo -e "    ${RED}•${RESET} ${dep}"
        done

        local apt_packages=()
        local dnf_packages=()
        local pacman_packages=()
        for dep in "${missing[@]}"; do
            if [[ "$dep" == "docker" || "$dep" == "docker-compose" ]]; then
                continue
            fi
            apt_packages+=("$(dependency_packages_for_manager "apt-get" "$dep")")
            dnf_packages+=("$(dependency_packages_for_manager "dnf" "$dep")")
            pacman_packages+=("$(dependency_packages_for_manager "pacman" "$dep")")
        done

        echo
        if [[ ${#apt_packages[@]} -gt 0 ]]; then
            local apt_install_packages
            local dnf_install_packages
            local pacman_install_packages
            join_words apt_install_packages "${apt_packages[@]}"
            join_words dnf_install_packages "${dnf_packages[@]}"
            join_words pacman_install_packages "${pacman_packages[@]}"

            echo "  On Ubuntu/Debian:  sudo apt-get install -y ${apt_install_packages}"
            echo "  On Fedora/RHEL:    sudo dnf install -y ${dnf_install_packages}"
            echo "  On Arch Linux:     sudo pacman -Sy --noconfirm --needed ${pacman_install_packages}"
        fi
        if docker_install_required; then
            echo "  For Docker/Compose: curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh ./get-docker.sh"
        fi
        echo "  Or run:            bash ensure_dependencies.sh"
        echo
        die "Please install the missing dependencies and re-run setup."
    fi

    success "All dependencies satisfied."
}
