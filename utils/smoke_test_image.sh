#!/usr/bin/env bash
#
# Start a built fake image and check that it is up, answers its control API,
# runs as a non-root user and, under docker, reports healthy.
#
#   utils/smoke_test_image.sh fake-core:dev
#
# RUNTIME picks the container CLI: docker (the default, and what CI uses) or
# Apple's container. SMOKE_PORT is the host port to publish on.

set -euo pipefail

# shellcheck source=SCRIPTDIR/exit_codes.sh
source "$(dirname "${BASH_SOURCE[0]}")/exit_codes.sh"

readonly RUNTIME="${RUNTIME:-docker}"
readonly SMOKE_PORT="${SMOKE_PORT:-18080}"
# The port the image listens on; the Dockerfile's PORT argument.
readonly CONTAINER_PORT=8080
readonly ROOT_UID=0
# How long the fake, and then docker's health check, get to come up.
readonly STARTUP_SECONDS=30
readonly POLL_SECONDS=1
readonly HTTP_NO_CONTENT=204

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <image>" >&2
  exit "${EXIT_USAGE}"
fi
readonly image="$1"
readonly name="fake-smoke-$$"
readonly base_url="http://127.0.0.1:${SMOKE_PORT}"

fail() {
  echo "❌ $*" >&2
  echo "--- container logs ---" >&2
  "${RUNTIME}" logs "${name}" >&2 || true
  exit "${EXIT_FAILURE}"
}

cleanup() {
  "${RUNTIME}" rm -f "${name}" >/dev/null 2>&1 || "${RUNTIME}" delete --force "${name}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${RUNTIME}" run -d --name "${name}" -p "127.0.0.1:${SMOKE_PORT}:${CONTAINER_PORT}" "${image}" >/dev/null

waited=0
until curl -fsS "${base_url}/healthz" >/dev/null 2>&1; do
  if (( waited >= STARTUP_SECONDS )); then
    fail "${image} did not answer /healthz within ${STARTUP_SECONDS}s"
  fi
  sleep "${POLL_SECONDS}"
  waited=$(( waited + POLL_SECONDS ))
done
echo "✅ /healthz answers"

status="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: application/json' -d '{"tokens": {"t": "smoke"}}' \
  "${base_url}/_fake/state")"
[[ "${status}" == "${HTTP_NO_CONTENT}" ]] || fail "PUT /_fake/state answered ${status}"
echo "✅ /_fake/state accepts a seed"

curl -fsS "${base_url}/_fake/requests" >/dev/null || fail "GET /_fake/requests failed"
echo "✅ /_fake/requests answers"

uid="$("${RUNTIME}" exec "${name}" id -u)"
[[ "${uid}" != "${ROOT_UID}" ]] || fail "the fake runs as root"
echo "✅ runs as uid ${uid}"

# Only docker runs HEALTHCHECK; Apple's container ignores it.
if [[ "${RUNTIME}" == "docker" ]]; then
  waited=0
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "${name}")" == "healthy" ]]; do
    if (( waited >= STARTUP_SECONDS )); then
      fail "docker never reported ${image} healthy"
    fi
    sleep "${POLL_SECONDS}"
    waited=$(( waited + POLL_SECONDS ))
  done
  echo "✅ docker reports it healthy"
fi
