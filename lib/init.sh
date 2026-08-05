#!/usr/bin/env bash
# lib/init.sh — load all easydeploy-lib modules
# Source this file from product repos; do not execute directly.

if [[ -n "${EASYDEPLOY_LIB_INIT:-}" ]]; then
    return 0 2>/dev/null || exit 0
fi
EASYDEPLOY_LIB_INIT=1

EASYDEPLOY_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=lib/core.sh
source "${EASYDEPLOY_LIB}/lib/core.sh"
# shellcheck source=lib/env.sh
source "${EASYDEPLOY_LIB}/lib/env.sh"
# shellcheck source=lib/prompt.sh
source "${EASYDEPLOY_LIB}/lib/prompt.sh"
# shellcheck source=lib/secrets.sh
source "${EASYDEPLOY_LIB}/lib/secrets.sh"
# shellcheck source=lib/template.sh
source "${EASYDEPLOY_LIB}/lib/template.sh"
# shellcheck source=lib/domain.sh
source "${EASYDEPLOY_LIB}/lib/domain.sh"
# shellcheck source=lib/pkgman.sh
source "${EASYDEPLOY_LIB}/lib/pkgman.sh"
# shellcheck source=lib/docker.sh
source "${EASYDEPLOY_LIB}/lib/docker.sh"
# shellcheck source=lib/deps.sh
source "${EASYDEPLOY_LIB}/lib/deps.sh"
