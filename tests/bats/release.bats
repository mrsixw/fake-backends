#!/usr/bin/env bats
#
# The release scripts, run against a throwaway git repo with the real, pinned
# git-mkver (make bats installs it) and a stub gh that records its calls.

REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
UTILS="${REPO_ROOT}/utils"

setup() {
  # shellcheck source=../../utils/exit_codes.sh
  source "${UTILS}/exit_codes.sh"
  export PATH="${REPO_ROOT}/.tools/bin:${BATS_TEST_DIRNAME}/stubs:${PATH}"
  export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
  export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
  export GH_STUB_LOG="${BATS_TEST_TMPDIR}/gh"
  mkdir -p "${GH_STUB_LOG}"

  cd "${BATS_TEST_TMPDIR}"
  git init -q -b main repo
  cd repo
  cp "${REPO_ROOT}/mkver.conf" .
  git add mkver.conf
  git commit -qm "feat: first"
}

commit() {
  git commit -q --allow-empty -m "$1"
}

# --- next_version.sh ---------------------------------------------------

@test "the first release is 0.1.0" {
  run "${UTILS}/next_version.sh"
  [ "${status}" -eq "${EXIT_OK}" ]
  [ "${output}" = $'version=0.1.0\nrelease=true' ]
}

@test "a fix after a release is a patch" {
  git tag v0.1.0
  commit "fix: something"
  run "${UTILS}/next_version.sh"
  [ "${output}" = $'version=0.1.1\nrelease=true' ]
}

@test "a docs-only change still releases a patch" {
  git tag v0.1.0
  commit "docs: something"
  run "${UTILS}/next_version.sh"
  [ "${output}" = $'version=0.1.1\nrelease=true' ]
}

@test "an already released HEAD is not released again" {
  git tag v0.1.0
  run "${UTILS}/next_version.sh"
  [ "${status}" -eq "${EXIT_OK}" ]
  [ "${output}" = $'version=0.1.0\nrelease=false' ]
}

@test "the report shows the last and next versions on the summary page" {
  git tag v0.1.0
  commit "feat: more"
  export GITHUB_STEP_SUMMARY="${BATS_TEST_TMPDIR}/summary"
  run "${UTILS}/next_version.sh" --report
  [ "${status}" -eq "${EXIT_OK}" ]
  [[ "${output}" == *"Last release: 0.1.0"* ]]
  [[ "${output}" == *"Next release: 0.2.0"* ]]
  [[ "${output}" != *"MAJOR"* ]]
  grep -q 'Next release: `0.2.0`' "${GITHUB_STEP_SUMMARY}"
}

@test "the report says when nothing has been released yet" {
  run "${UTILS}/next_version.sh" --report
  [[ "${output}" == *"Last release: none"* ]]
}

@test "the report warns about a major bump" {
  git tag v0.1.0
  commit "feat!: drop something"
  run "${UTILS}/next_version.sh" --report
  [ "${status}" -eq "${EXIT_OK}" ]
  [[ "${output}" == *"MAJOR version bump: 0.1.0 → 1.0.0"* ]]
}

@test "an unknown argument is a usage error" {
  run "${UTILS}/next_version.sh" --bogus
  [ "${status}" -eq "${EXIT_USAGE}" ]
}

# --- create_release.sh -------------------------------------------------

@test "a release with no image digests is refused" {
  export GITHUB_REPOSITORY=MrSixW/fake-backends
  mkdir digests
  run "${UTILS}/create_release.sh" 1.2.3 digests
  [ "${status}" -eq "${EXIT_FAILURE}" ]
  [[ "${output}" == *"refusing to release without images"* ]]
  [ ! -e "${GH_STUB_LOG}/release-args" ]
}

@test "the release lists each image pinned by digest, then the generated notes" {
  export GITHUB_REPOSITORY=MrSixW/fake-backends
  mkdir digests
  echo "sha256:aaa" > digests/fake-github
  echo "sha256:bbb" > digests/fake-jira
  run "${UTILS}/create_release.sh" 1.2.3 digests
  [ "${status}" -eq "${EXIT_OK}" ]

  grep -qx -- '- `ghcr.io/mrsixw/fake-github:1.2.3@sha256:aaa`' "${GH_STUB_LOG}/notes"
  grep -qx -- '- `ghcr.io/mrsixw/fake-jira:1.2.3@sha256:bbb`' "${GH_STUB_LOG}/notes"
  grep -qx "generated notes" "${GH_STUB_LOG}/notes"

  args="$(cat "${GH_STUB_LOG}/release-args")"
  [[ "${args}" == "release create v1.2.3 "* ]]
  [[ "${args}" == *"--target $(git rev-parse HEAD)"* ]]
}
