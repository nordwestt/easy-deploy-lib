#!/usr/bin/env bash
# lib/env.sh — safe deploy env loading (easydeploy-lib)

load_deploy_env() {
    local deploy_env="$1"
    [[ -f "$deploy_env" ]] || return 0

    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}"
        export "${key}=${value}"
    done < "$deploy_env"
}
