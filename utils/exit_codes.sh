# shellcheck shell=bash
# shellcheck disable=SC2034  # Constants for the scripts that source this file.
#
# Exit codes shared by the scripts in utils/ and the workflows that read them.
#
#   source utils/exit_codes.sh
#   exit "${EXIT_FAILURE}"

# Sourcing twice would redeclare readonly variables, which is an error.
[[ -n "${EXIT_CODES_SOURCED:-}" ]] && return
readonly EXIT_CODES_SOURCED=yes

readonly EXIT_OK=0
readonly EXIT_FAILURE=1
readonly EXIT_USAGE=2
