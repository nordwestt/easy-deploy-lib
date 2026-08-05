#!/usr/bin/env bash
# lib/core.sh — colors and logging (easydeploy-lib)

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}  -->${RESET} $*"; }
success() { echo -e "${GREEN}  [ok]${RESET} $*"; }
warn()    { echo -e "${YELLOW}  [!]${RESET}  $*"; }
error()   { echo -e "${RED}  [ERR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
