#!/usr/bin/env bash
# lib/prompt.sh — interactive prompts (easydeploy-lib)

ask() {
    local _var="$1"
    local _prompt="$2"
    local _default="${3:-}"

    if [[ -n "$_default" ]]; then
        echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${_default}]${RESET}: "
    else
        echo -ne "${BOLD}  ${_prompt}${RESET}: "
    fi

    local _input
    read -r _input
    printf -v "$_var" '%s' "${_input:-$_default}"
}

ask_secret() {
    local _var="$1"
    local _prompt="$2"

    echo -ne "${BOLD}  ${_prompt}${RESET}: "
    local _input
    read -rs _input
    echo
    printf -v "$_var" '%s' "$_input"
}

ask_yn() {
    local _var="$1"
    local _prompt="$2"
    local _default="${3:-n}"

    local _hint
    if [[ "$_default" == "y" ]]; then _hint="Y/n"; else _hint="y/N"; fi

    echo -ne "${BOLD}  ${_prompt}${RESET} ${CYAN}[${_hint}]${RESET}: "
    local _input
    read -r _input
    _input="${_input:-$_default}"
    _input="${_input,,}"

    case "$_input" in
        y|yes) printf -v "$_var" 'y' ;;
        *)     printf -v "$_var" 'n' ;;
    esac
}
