#!/usr/bin/env bash
#
# Work out the version a release of HEAD carries, and whether HEAD still
# needs releasing. Prints two lines in GITHUB_OUTPUT form:
#
#   version=1.4.0
#   release=true
#
# release is false when HEAD already carries a v* tag: a re-run of a release
# that finished, which must not try to publish the same version twice.
#
# With --report it instead prints the last and next versions for a pull
# request, warning when the next one is a new major, and adds them to the
# run's summary page.
#
# Needs the full history and every tag (actions/checkout fetch-depth: 0).

set -euo pipefail

# shellcheck source=SCRIPTDIR/exit_codes.sh
source "$(dirname "${BASH_SOURCE[0]}")/exit_codes.sh"

readonly TAG_PREFIX="v"
readonly SEMVER_CORE='^([0-9]+\.[0-9]+\.[0-9]+)'
readonly NO_RELEASE_YET="none"

report=false
case "${1:-}" in
  "") ;;
  --report) report=true ;;
  *)
    echo "usage: $0 [--report]" >&2
    exit "${EXIT_USAGE}"
    ;;
esac

if ! raw_next="$(git mkver next)"; then
  echo "git mkver could not compute the next version (is mkver.conf valid?)" >&2
  exit "${EXIT_FAILURE}"
fi
# mkver may still append a pre-release or build suffix; images want X.Y.Z.
raw_next="$(printf '%s' "${raw_next}" | tr -d '[:space:]')"
if [[ ! "${raw_next}" =~ ${SEMVER_CORE} ]]; then
  echo "could not read a version out of git mkver: ${raw_next}" >&2
  exit "${EXIT_FAILURE}"
fi
next="${BASH_REMATCH[1]}"

if [[ "${report}" == false ]]; then
  existing="$(git tag --points-at HEAD --list "${TAG_PREFIX}*" | head -n 1)"
  if [[ -n "${existing}" ]]; then
    printf 'version=%s\nrelease=false\n' "${existing#"${TAG_PREFIX}"}"
  else
    printf 'version=%s\nrelease=true\n' "${next}"
  fi
  exit "${EXIT_OK}"
fi

last="$(git describe --tags --abbrev=0 --match "${TAG_PREFIX}*" 2>/dev/null || true)"
last="${last#"${TAG_PREFIX}"}"
last="${last:-${NO_RELEASE_YET}}"

summary="Last release: \`${last}\`"$'\n'"Next release: \`${next}\`"
echo "Last release: ${last}"
echo "Next release: ${next}"

if [[ "${last}" != "${NO_RELEASE_YET}" && "${last%%.*}" != "${next%%.*}" ]]; then
  warning="⚠️ MAJOR version bump: ${last} → ${next}. A commit since the last release used '!' after its type, or the breaking-change footer, anywhere in its message (mkver matches either as a substring, so quoting one counts). Reword it if that was not intended."
  echo "${warning}"
  summary="${summary}"$'\n\n'"${warning}"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '### Next release version\n\n%s\n' "${summary}" >> "${GITHUB_STEP_SUMMARY}"
fi
