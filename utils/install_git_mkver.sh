#!/usr/bin/env bash
#
# Install a pinned, checksum-verified git-mkver.
#
#   utils/install_git_mkver.sh                 # into /usr/local/bin (CI)
#   INSTALL_DIR=.tools/bin utils/install_git_mkver.sh
#
# Pinned so the same commit always runs the same release logic. To upgrade,
# change MKVER_VERSION and both checksums together (shasum -a 256 on each
# release asset).

set -euo pipefail

# shellcheck source=SCRIPTDIR/exit_codes.sh
source "$(dirname "${BASH_SOURCE[0]}")/exit_codes.sh"

readonly MKVER_VERSION="2.0.0"
readonly LINUX_SHA256="7a97c6af78233459913ab5313c3dea37e181347f3cf505ad1cbb9432b79e7bcc"
readonly DARWIN_SHA256="0b1faf04a5fa410be0607658affcf73f1fab5d6a7a0b7d03d3ab82bd0e4f245c"
readonly INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    platform="linux-x86_64"
    expected="${LINUX_SHA256}"
    ;;
  Darwin-*)
    platform="darwin-universal"
    expected="${DARWIN_SHA256}"
    ;;
  *)
    echo "no git-mkver ${MKVER_VERSION} build for $(uname -s) $(uname -m)" >&2
    exit "${EXIT_FAILURE}"
    ;;
esac

asset="git-mkver-${platform}-${MKVER_VERSION}.tar.gz"
url="https://github.com/idc101/git-mkver/releases/download/v${MKVER_VERSION}/${asset}"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

curl -fsSL -o "${work}/${asset}" "${url}"
actual="$(shasum -a 256 "${work}/${asset}" | cut -d ' ' -f 1)"
if [[ "${actual}" != "${expected}" ]]; then
  echo "checksum mismatch for ${asset}: expected ${expected}, got ${actual}" >&2
  exit "${EXIT_FAILURE}"
fi

tar -xzf "${work}/${asset}" -C "${work}"
mkdir -p "${INSTALL_DIR}"
install -m 0755 "${work}/git-mkver" "${INSTALL_DIR}/git-mkver"
echo "installed git-mkver ${MKVER_VERSION} to ${INSTALL_DIR}"
