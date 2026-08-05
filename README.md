# easydeploy-lib

Shared Bash infrastructure for **easy deploy** product repositories (Matrix Easy Deploy, OpenCloud Easy Deploy, and future kits).

## Usage in a product repo

Add as a git submodule at `easydeploy-lib/`:

```bash
git submodule add https://github.com/your-org/easydeploy-lib.git easydeploy-lib
git submodule update --init --recursive
```

In product scripts:

```bash
source "${SCRIPT_DIR}/easydeploy-lib/lib/init.sh"
source "${SCRIPT_DIR}/scripts/deps_config.sh"   # defines easydeploy_required_deps
```

Clone products with:

```bash
git clone --recurse-submodules <product-repo-url>
```

## Modules

| File | Purpose |
|------|---------|
| `lib/core.sh` | Colors, logging, `die` |
| `lib/env.sh` | Safe `.env` loading |
| `lib/prompt.sh` | Interactive `ask` helpers |
| `lib/secrets.sh` | `generate_secret` |
| `lib/template.sh` | `{{KEY}}` template rendering |
| `lib/domain.sh` | Base domain from FQDN |
| `lib/docker.sh` | Compose helper, networks/volumes, Docker install |
| `lib/pkgman.sh` | OS package manager detection and installs |
| `lib/deps.sh` | Dependency check/install framework |
| `lib/init.sh` | Sources all modules above |

## Product hooks

Define in `scripts/deps_config.sh`:

```bash
easydeploy_required_deps() {
    printf '%s\n' docker docker-compose openssl curl python3
}
```

Optional override for package name mapping:

```bash
easydeploy_dependency_packages_for_manager() {
    local manager="$1" dep="$2"
    case "${manager}:${dep}" in
        apt-get:git) echo "git" ;;
        *) return 1 ;;
    esac
}
```

Return 1 from the override to fall back to built-in mappings in `lib/deps.sh`.

## Versioning

Tag releases as `v0.1.0`, etc. Product repos pin the submodule commit and bump in dedicated commits.

## Release archives

When building source tarballs, initialize submodules first:

```bash
git submodule update --init --recursive
git archive --format=tar.gz --prefix=my-product/ HEAD easydeploy-lib
# Or use a script that copies easydeploy-lib into the archive tree.
```

GitHub’s default “Source code” zip for a tag does **not** include submodule contents unless you bundle them explicitly.
