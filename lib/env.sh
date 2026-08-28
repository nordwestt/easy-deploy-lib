#!/usr/bin/env bash
# lib/env.sh — safe deploy env loading (easydeploy-lib)

# Parent `uv run` / venv (e.g. easydeploy-engine apply spawning a kit) must not
# leak into a nested project's `uv run`. Never hardcode a login name here.
clear_parent_python_env() {
    unset VIRTUAL_ENV
    unset VIRTUAL_ENV_PROMPT
    unset PYTHONHOME
    unset UV_PROJECT
    unset UV_PROJECT_ENVIRONMENT
    unset UV_PYTHON
    unset UV_ACTIVE
}

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
