#!/usr/bin/env bash
set -euo pipefail

release_dir="${1:?usage: activate-release.sh RELEASE_DIR CURRENT_LINK [HEALTH_PORT]}"
current_link="${2:?usage: activate-release.sh RELEASE_DIR CURRENT_LINK [HEALTH_PORT]}"
health_port="${3:-8080}"

if [[ ! "$health_port" =~ ^[1-9][0-9]{0,4}$ ]] || ((health_port > 65535)); then
  echo "invalid health port" >&2
  exit 64
fi

env_file="$release_dir/infra/environments/prod/.env"
base_compose="$release_dir/infra/compose.yml"
prod_compose="$release_dir/infra/environments/prod/compose.yml"
for file in "$env_file" "$base_compose" "$prod_compose"; do
  [[ -r "$file" ]] || { echo "missing release file: $file" >&2; exit 66; }
done

compose=(
  docker compose
  --env-file "$env_file"
  -f "$base_compose"
  -f "$prod_compose"
)
previous_release=""
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink "$current_link")"
fi

rollback() {
  [[ -n "$previous_release" && -d "$previous_release" ]] || return 0
  local previous_env="$previous_release/infra/environments/prod/.env"
  local previous_base="$previous_release/infra/compose.yml"
  local previous_prod="$previous_release/infra/environments/prod/compose.yml"
  docker compose --env-file "$previous_env" -f "$previous_base" -f "$previous_prod" \
    up --detach --remove-orphans --wait || true
}

"${compose[@]}" config --quiet
"${compose[@]}" pull
if ! "${compose[@]}" up --detach --remove-orphans --wait; then
  rollback
  echo "production activation failed; previous release was restored" >&2
  exit 1
fi

healthy=false
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 \
    "http://127.0.0.1:${health_port}/readyz" >/dev/null; then
    healthy=true
    break
  fi
  sleep 2
done
if [[ "$healthy" != true ]]; then
  "${compose[@]}" logs --no-color --tail 200 >&2 || true
  rollback
  echo "production readiness failed; previous release was restored" >&2
  exit 1
fi

ln -sfn "$release_dir" "$current_link"
printf 'PROD_ACTIVATE_PASS release=%s health_port=%s\n' "$release_dir" "$health_port"
