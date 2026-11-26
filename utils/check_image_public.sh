#!/usr/bin/env bash
#
# Check that anyone can pull an image from GHCR without logging in.
#
#   utils/check_image_public.sh mrsixw/fake-github 1.4.0
#
# A new GHCR package can start out private, and there is no API to change
# that: it is a one-time switch in the package's settings. The release job
# runs this before creating the release, so a release whose images consumers
# cannot pull is never announced.

set -euo pipefail

# shellcheck source=SCRIPTDIR/exit_codes.sh
source "$(dirname "${BASH_SOURCE[0]}")/exit_codes.sh"

readonly REGISTRY="${REGISTRY:-ghcr.io}"
readonly HTTP_OK=200
readonly MANIFEST_TYPES="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <owner/image> <tag>" >&2
  exit "${EXIT_USAGE}"
fi
readonly repository="$1"
readonly tag="$2"
readonly package="${repository#*/}"
readonly owner="${repository%%/*}"

not_public() {
  echo "❌ ${REGISTRY}/${repository}:${tag} cannot be pulled anonymously." >&2
  echo "Make the package public once, then re-run this job:" >&2
  echo "  https://github.com/users/${owner}/packages/container/${package}/settings" >&2
  exit "${EXIT_FAILURE}"
}

# GHCR hands out an anonymous pull token for public repositories only.
token_json="$(curl -fsS "https://${REGISTRY}/token?scope=repository:${repository}:pull")" || not_public
token="$(printf '%s' "${token_json}" | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')" || not_public

status="$(curl -sS -o /dev/null -w '%{http_code}' --head \
  -H "Authorization: Bearer ${token}" \
  -H "Accept: ${MANIFEST_TYPES}" \
  "https://${REGISTRY}/v2/${repository}/manifests/${tag}")"
[[ "${status}" == "${HTTP_OK}" ]] || not_public

echo "✅ ${REGISTRY}/${repository}:${tag} is public"
