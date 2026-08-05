#!/usr/bin/env bash
# lib/pkgman.sh — OS package manager helpers (easydeploy-lib)

run_as_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    elif command -v sudo &>/dev/null; then
        sudo "$@"
    else
        die "Need root privileges for: $* — re-run with sudo or as root"
    fi
}

detect_supported_package_manager() {
    if command -v apt-get &>/dev/null; then
        echo "apt-get"
        return 0
    fi

    if command -v dnf &>/dev/null; then
        echo "dnf"
        return 0
    fi

    if command -v pacman &>/dev/null; then
        echo "pacman"
        return 0
    fi

    return 1
}

command_prefix_for_privileged_install() {
    local output_var="$1"

    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        printf -v "$output_var" '%s' ""
        return 0
    fi

    if ! command -v sudo &>/dev/null; then
        die "Installing dependencies requires root privileges or sudo."
    fi

    printf -v "$output_var" '%s' "sudo"
}

install_packages_with_manager() {
    local manager="$1"
    local prefix_cmd="$2"
    shift 2
    local packages=("$@")

    [[ ${#packages[@]} -gt 0 ]] || return 0

    case "$manager" in
        apt-get)
            if [[ -n "$prefix_cmd" ]]; then
                DEBIAN_FRONTEND=noninteractive "$prefix_cmd" apt-get update
                DEBIAN_FRONTEND=noninteractive "$prefix_cmd" apt-get install -y "${packages[@]}"
            else
                DEBIAN_FRONTEND=noninteractive apt-get update
                DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
            fi
            ;;
        dnf)
            if [[ -n "$prefix_cmd" ]]; then
                "$prefix_cmd" dnf install -y "${packages[@]}"
            else
                dnf install -y "${packages[@]}"
            fi
            ;;
        pacman)
            if [[ -n "$prefix_cmd" ]]; then
                "$prefix_cmd" pacman -Sy --noconfirm --needed "${packages[@]}"
            else
                pacman -Sy --noconfirm --needed "${packages[@]}"
            fi
            ;;
        *)
            die "Unsupported package manager: ${manager}"
            ;;
    esac
}
