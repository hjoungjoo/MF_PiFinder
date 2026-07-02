#!/usr/bin/env bash
set -euo pipefail

# LX200 OnStepX INDI test installer.
#
# This wrapper intentionally reuses install_indi_mount.sh so the INDI Web
# Manager install and service setup stay identical to the stable installer.
# A separate default build root avoids reusing an existing ~/indi v2.1.6 tree.
# It also enables the bundled PiFinder INDI patch set, including LX200 OnStepX.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sanitize_arm64_flags() {
    local flags="${1:-}"
    flags="$(printf '%s\n' "${flags}" | sed -E 's/(^|[[:space:]])-m(arch|cpu|tune)=[^[:space:]]+//g')"
    printf '%s' "${flags}"
}

export INDI_VERSION="${INDI_VERSION:-v2.2.3.1}"
export INDI_3RDPARTY_VERSION="${INDI_3RDPARTY_VERSION:-v2.2.3.1}"
export PYINDI_VERSION="${PYINDI_VERSION:-v2.1.2}"
export BUILD_ROOT="${BUILD_ROOT:-$HOME/indi-latest}"
export INDI_PATCH_DIR="${INDI_PATCH_DIR:-${SCRIPT_DIR}/patches}"
export CFLAGS="$(sanitize_arm64_flags "${CFLAGS:-}") -march=armv8-a"
export CXXFLAGS="$(sanitize_arm64_flags "${CXXFLAGS:-}") -march=armv8-a -Wno-error=stringop-overread -Wno-error=stringop-truncation"

echo "PiFinder LX200 OnStepX INDI mount-control installer"
echo "Using BUILD_ROOT=${BUILD_ROOT}"
echo "INDI_VERSION=${INDI_VERSION}"
echo "INDI_3RDPARTY_VERSION=${INDI_3RDPARTY_VERSION}"
echo "PYINDI_VERSION=${PYINDI_VERSION}"
echo "INDI_PATCH_DIR=${INDI_PATCH_DIR}"
echo "CFLAGS=${CFLAGS}"
echo "CXXFLAGS=${CXXFLAGS}"
echo

mkdir -p "${BUILD_ROOT}"
exec "${SCRIPT_DIR}/install_indi_mount.sh"
