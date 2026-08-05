#!/usr/bin/env bash
# lib/domain.sh — domain helpers (easydeploy-lib)

extract_base_domain() {
    local fqdn="$1"
    echo "$fqdn" | awk -F. '{
        n=NF;
        if(n>=3) { for(i=2;i<=n;i++) printf "%s%s",$i,(i<n?".":""); print "" }
        else print $0
    }'
}

base_domain_from_host() {
    extract_base_domain "$1"
}
