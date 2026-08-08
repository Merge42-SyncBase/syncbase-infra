#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_EC2_HOST
  SYNCBASE_EC2_USER
  SYNCBASE_EC2_SSH_KEY_FILE
  SYNCBASE_EC2_KNOWN_HOSTS_FILE
  SYNCBASE_PROD_ENV_FILE
  SYNCBASE_PROD_MCP_TOKEN_FILE
  SYNCBASE_RELEASE_ID
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

for file in \
  "$SYNCBASE_EC2_SSH_KEY_FILE" \
  "$SYNCBASE_EC2_KNOWN_HOSTS_FILE" \
  "$SYNCBASE_PROD_ENV_FILE" \
  "$SYNCBASE_PROD_MCP_TOKEN_FILE"; do
  [[ -r "$file" ]] || { echo "unreadable deployment file: $file" >&2; exit 66; }
done

[[ "$SYNCBASE_EC2_HOST" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid EC2 host" >&2; exit 64; }
[[ "$SYNCBASE_EC2_USER" =~ ^[A-Za-z_][A-Za-z0-9._-]*$ ]] || { echo "invalid EC2 user" >&2; exit 64; }
[[ "$SYNCBASE_RELEASE_ID" =~ ^[A-Za-z0-9._-]{1,96}$ ]] || { echo "invalid release id" >&2; exit 64; }

remote_root="${SYNCBASE_EC2_DEPLOY_ROOT:-syncbase}"
[[ "$remote_root" =~ ^[A-Za-z0-9._/-]+$ && "$remote_root" != /* && "$remote_root" != *..* ]] || {
  echo "SYNCBASE_EC2_DEPLOY_ROOT must be a safe path relative to the remote home" >&2
  exit 64
}
health_port="${SYNCBASE_PROD_HEALTH_PORT:-8080}"
if [[ ! "$health_port" =~ ^[1-9][0-9]{0,4}$ ]] || ((health_port > 65535)); then
  echo "invalid production health port" >&2
  exit 64
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
for path in \
  "$project_root/infra/build/models/multilingual-e5-small/model.onnx" \
  "$project_root/infra/build/models/multilingual-e5-small/tokenizer.json" \
  "$project_root/infra/build/runtime"; do
  [[ -e "$path" ]] || { echo "missing production artifact: $path" >&2; exit 66; }
done

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/syncbase-ec2-deploy.XXXXXX")"
trap 'rm -rf "$temporary_dir"' EXIT
stage="$temporary_dir/release"
mkdir -p \
  "$stage/infra/environments/prod/secrets" \
  "$stage/infra/build/models" \
  "$stage/infra/deploy"
cp "$project_root/infra/compose.yml" "$stage/infra/compose.yml"
cp -R "$project_root/infra/postgres" "$stage/infra/postgres"
cp "$project_root/infra/environments/prod/compose.yml" \
  "$stage/infra/environments/prod/compose.yml"
cp "$SYNCBASE_PROD_ENV_FILE" "$stage/infra/environments/prod/.env"
cp "$SYNCBASE_PROD_MCP_TOKEN_FILE" \
  "$stage/infra/environments/prod/secrets/mcp-token"
cp -R "$project_root/infra/build/models/multilingual-e5-small" \
  "$stage/infra/build/models/multilingual-e5-small"
cp -R "$project_root/infra/build/runtime" "$stage/infra/build/runtime"
cp "$project_root/infra/deploy/activate-release.sh" "$stage/infra/deploy/activate-release.sh"
chmod 0600 \
  "$stage/infra/environments/prod/.env" \
  "$stage/infra/environments/prod/secrets/mcp-token"
chmod 0755 "$stage/infra/deploy/activate-release.sh"

archive="$temporary_dir/release.tar.gz"
tar -C "$stage" -czf "$archive" .

ssh_options=(
  -i "$SYNCBASE_EC2_SSH_KEY_FILE"
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SYNCBASE_EC2_KNOWN_HOSTS_FILE"
)
remote="$SYNCBASE_EC2_USER@$SYNCBASE_EC2_HOST"
remote_archive=".syncbase-${SYNCBASE_RELEASE_ID}.tar.gz"

scp "${ssh_options[@]}" "$archive" "$remote:$remote_archive"

if [[ -n "${SYNCBASE_REGISTRY_TOKEN_FILE:-}" ]]; then
  [[ -r "$SYNCBASE_REGISTRY_TOKEN_FILE" ]] || {
    echo "SYNCBASE_REGISTRY_TOKEN_FILE is unreadable" >&2
    exit 66
  }
  registry_host="${SYNCBASE_REGISTRY_HOST:-ghcr.io}"
  registry_user="${SYNCBASE_REGISTRY_USERNAME:?required with registry token}"
  [[ "$registry_host" =~ ^[A-Za-z0-9._:-]+$ ]] || { echo "invalid registry host" >&2; exit 64; }
  [[ "$registry_user" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid registry user" >&2; exit 64; }
  registry_login=(docker login "$registry_host" --username "$registry_user" --password-stdin)
  # Values expanded into the remote command are restricted to safe identifier characters above.
  # shellcheck disable=SC2029
  ssh "${ssh_options[@]}" "$remote" "${registry_login[@]}" \
    <"$SYNCBASE_REGISTRY_TOKEN_FILE"
fi

ssh "${ssh_options[@]}" "$remote" bash -s -- \
  "$remote_root" "$SYNCBASE_RELEASE_ID" "$remote_archive" "$health_port" <<'REMOTE'
set -euo pipefail
remote_root="$1"
release_id="$2"
remote_archive="$3"
health_port="$4"
deployment_root="$HOME/$remote_root"
release_dir="$deployment_root/releases/$release_id"
current_link="$deployment_root/current"
mkdir -p "$release_dir"
tar -xzf "$HOME/$remote_archive" -C "$release_dir"
rm -f "$HOME/$remote_archive"
"$release_dir/infra/deploy/activate-release.sh" \
  "$release_dir" "$current_link" "$health_port"
REMOTE

printf 'EC2_DEPLOY_PASS host=%s release=%s\n' "$SYNCBASE_EC2_HOST" "$SYNCBASE_RELEASE_ID"
