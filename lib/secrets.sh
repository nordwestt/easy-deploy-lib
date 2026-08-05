#!/usr/bin/env bash
# lib/secrets.sh — secret generation (easydeploy-lib)

generate_secret() {
    openssl rand -hex 32
}
