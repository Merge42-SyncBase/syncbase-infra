#!/usr/bin/env bash
set -euo pipefail

image="${1:?usage: resolve-image-digest.sh IMAGE_REFERENCE}"
[[ "$image" != *@* ]] || { echo "image must be a tag reference, not a digest" >&2; exit 64; }

digest="$(docker buildx imagetools inspect "$image" --format '{{json .Manifest}}' | jq -r '.digest // empty')"
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "could not resolve a manifest digest for $image" >&2
  exit 1
fi
printf '%s@%s\n' "${image%%@*}" "$digest"
