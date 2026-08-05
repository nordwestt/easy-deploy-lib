#!/usr/bin/env bash
# lib/template.sh — {{KEY}} template rendering (easydeploy-lib)

render_template() {
    local src="$1"
    local dest="$2"
    local vars_file="$3"

    cp "$src" "$dest"

    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        local esc_value
        esc_value=$(printf '%s\n' "$value" | sed 's/[&/\]/\\&/g')
        sed -i "s|{{${key}}}|${esc_value}|g" "$dest"
    done < "$vars_file"
}
