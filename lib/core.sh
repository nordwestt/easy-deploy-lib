#!/usr/bin/env bash
# lib/core.sh — colors and logging (easydeploy-lib)

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

easydeploy_verbose() {
    case "${EASYDEPLOY_VERBOSE:-}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

easydeploy_quiet() {
    easydeploy_verbose && return 1
    case "${EASYDEPLOY_QUIET:-}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

info() {
    easydeploy_quiet && return 0
    echo -e "${CYAN}  -->${RESET} $*"
}
success() {
    easydeploy_quiet && return 0
    echo -e "${GREEN}  [ok]${RESET} $*"
}
warn()    { echo -e "${YELLOW}  [!]${RESET}  $*"; }
error()   { echo -e "${RED}  [ERR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
