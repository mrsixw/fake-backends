#!/usr/bin/env bash
#
# Tag HEAD and create its GitHub release, listing each published image's
# digest so consumers can pin it.
#
#   utils/create_release.sh 1.4.0 digests/
#
# The directory holds one file per image, named after it (fake-github), and
# containing the digest of its multi-arch manifest list (sha256:...). The
# release job runs this only after every image has been pushed, so a release
# exists only when its images do.
#
# Needs GH_TOKEN (the workflow's GITHUB_TOKEN, with contents: write) and
# GITHUB_REPOSITORY.

set -euo pipefail

# shellcheck source=SCRIPTDIR/exit_codes.sh
source "$(dirname "${BASH_SOURCE[0]}")/exit_codes.sh"

readonly REGISTRY="ghcr.io"
readonly TAG_PREFIX="v"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <version> <digest-dir>" >&2
  exit "${EXIT_USAGE}"
fi
readonly version="$1"
readonly digest_dir="$2"
readonly tag="${TAG_PREFIX}${version}"
target="$(git rev-parse HEAD)"
readonly target
# GHCR image names must be lower case.
owner="$(printf '%s' "${GITHUB_REPOSITORY%%/*}" | tr '[:upper:]' '[:lower:]')"
readonly owner
notes="$(mktemp)"
trap 'rm -f "${notes}"' EXIT

shopt -s nullglob
digest_files=("${digest_dir}"/*)
if [[ ${#digest_files[@]} -eq 0 ]]; then
  echo "no image digests in ${digest_dir}; refusing to release without images" >&2
  exit "${EXIT_FAILURE}"
fi

{
  printf '## Images\n\n'
  printf 'Pin by digest:\n\n'
  for file in "${digest_files[@]}"; do
    image="$(basename "${file}")"
    digest="$(tr -d '[:space:]' < "${file}")"
    # shellcheck disable=SC2016  # The backticks are Markdown, not a command.
    printf -- '- `%s/%s/%s:%s@%s`\n' "${REGISTRY}" "${owner}" "${image}" "${version}" "${digest}"
  done
  printf '\n'
  gh api "repos/${GITHUB_REPOSITORY}/releases/generate-notes" \
    -f tag_name="${tag}" -f target_commitish="${target}" --jq .body
} > "${notes}"

gh release create "${tag}" \
  --title "${tag}" \
  --target "${target}" \
  --notes-file "${notes}"
