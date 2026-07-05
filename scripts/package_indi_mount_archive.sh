#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${BUILD_ROOT:-$HOME/indi-latest}"
OUT_DIR="${OUT_DIR:-${REPO_ROOT}/dist}"
INDI_BUILD_DIR="${INDI_BUILD_DIR:-${BUILD_ROOT}/indi/build}"
INDI_3RDPARTY_BUILD_DIR="${INDI_3RDPARTY_BUILD_DIR:-${BUILD_ROOT}/indi-3rdparty/build-drivers}"
INDI_MANIFEST="${INDI_BUILD_DIR}/install_manifest.txt"
INDI_3RDPARTY_MANIFEST="${INDI_3RDPARTY_BUILD_DIR}/install_manifest.txt"
PYTHON_SITE="${PYTHON_SITE:-$(python3 -c 'import site; print(site.getsitepackages()[0])')}"

require_file() {
    if [ ! -f "$1" ]; then
        echo "Missing required file: $1" >&2
        exit 1
    fi
}

copy_path_to_rootfs() {
    local path="$1"
    local rootfs="$2"
    local dest

    if [ ! -e "${path}" ] && [ ! -L "${path}" ]; then
        echo "WARNING: missing installed path: ${path}" >&2
        return 0
    fi

    dest="${rootfs}${path}"
    mkdir -p "$(dirname "${dest}")"
    cp -a "${path}" "${dest}"
}

copy_manifest_paths() {
    local manifest="$1"
    local rootfs="$2"

    while IFS= read -r path; do
        [ -n "${path}" ] || continue
        copy_path_to_rootfs "${path}" "${rootfs}"
    done < "${manifest}"
}

check_arm64_compatibility() {
    local report="$1"
    local cache
    local cpu_flag_pattern='-m(arch|cpu|tune)=(native|cortex-a76|armv8\.2-a|armv8\.3-a|armv8\.4-a|armv9[^[:space:]]*)'

    : > "${report}"
    {
        echo "PiFinder INDI archive compatibility report"
        echo "Generated: $(date -Is)"
        echo "Host machine: $(uname -m)"
        echo
    } >> "${report}"

    if [ "$(uname -m)" != "aarch64" ]; then
        echo "ERROR: this package must be created on aarch64/arm64." | tee -a "${report}" >&2
        exit 1
    fi

    for cache in "${INDI_BUILD_DIR}/CMakeCache.txt" "${INDI_3RDPARTY_BUILD_DIR}/CMakeCache.txt"; do
        require_file "${cache}"
        {
            echo "Checking CMake flags: ${cache}"
            grep -E 'CMAKE_(C|CXX)_FLAGS' "${cache}" || true
            echo
        } >> "${report}"
        if grep -Eq -- "${cpu_flag_pattern}" "${cache}"; then
            echo "ERROR: Pi 5-specific/native CPU flags found in ${cache}" | tee -a "${report}" >&2
            exit 1
        fi
    done

    for binary in /usr/bin/indiserver /usr/bin/indi_lx200generic /usr/bin/indi_lx200_OnStep /usr/bin/indi_lx200_OnStepX; do
        if [ -e "${binary}" ] || [ -L "${binary}" ]; then
            echo "file ${binary}: $(file -b "${binary}")" >> "${report}"
        fi
    done
}

require_file "${INDI_MANIFEST}"
require_file "${INDI_3RDPARTY_MANIFEST}"

mkdir -p "${OUT_DIR}"
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

ROOTFS="${STAGING}/rootfs"
METADATA="${STAGING}/metadata"
mkdir -p "${ROOTFS}" "${METADATA}"

check_arm64_compatibility "${METADATA}/compatibility_report.txt"

copy_manifest_paths "${INDI_MANIFEST}" "${ROOTFS}"
copy_manifest_paths "${INDI_3RDPARTY_MANIFEST}" "${ROOTFS}"

while IFS= read -r path; do
    copy_path_to_rootfs "${path}" "${ROOTFS}"
done < <(find /usr/bin -maxdepth 1 -type l \( -lname indi_lx200generic -o -lname indi_tcfs_focus \) -print | sort)

for path in \
    "${PYTHON_SITE}/PyIndi.py" \
    "${PYTHON_SITE}/_PyIndi.cpython-311-aarch64-linux-gnu.so" \
    "${PYTHON_SITE}/pyindi_client-2.1.2.dist-info" \
    "${PYTHON_SITE}/indiweb" \
    "${PYTHON_SITE}/indiweb-1.0.0.dist-info" \
    /usr/local/bin/indi-web; do
    copy_path_to_rootfs "${path}" "${ROOTFS}"
done

{
    echo "archive_format=mf-pifinder-indi-binary-v1"
    echo "created_at=$(date -Is)"
    echo "host=$(hostname)"
    echo "machine=$(uname -m)"
    echo "python_site=${PYTHON_SITE}"
    echo "indi_core_tag=$(git -C "${BUILD_ROOT}/indi" describe --tags --exact-match HEAD 2>/dev/null || git -C "${BUILD_ROOT}/indi" rev-parse --short HEAD)"
    echo "indi_3rdparty_tags=$(git -C "${BUILD_ROOT}/indi-3rdparty" tag --points-at HEAD 2>/dev/null | tr '\n' ' ')"
    echo "pyindi_version=2.1.2"
    echo "indiweb_version=1.0.0"
    echo "arm64_compatibility=checked-no-native-or-pi5-specific-cpu-flags"
    echo "recommended_build_flag=-march=armv8-a"
    if [ -f "${REPO_ROOT}/scripts/patches/indi-v2.2.3.1-onstepx.patch" ]; then
        echo "onstepx_patch=indi-v2.2.3.1-onstepx.patch"
        echo "onstepx_patch_sha256=$(sha256sum "${REPO_ROOT}/scripts/patches/indi-v2.2.3.1-onstepx.patch" | awk '{print $1}')"
    fi
} > "${METADATA}/build_info.txt"

ldd /usr/bin/indiserver > "${METADATA}/ldd-indiserver.txt"
ldd /usr/bin/indi_lx200generic > "${METADATA}/ldd-indi_lx200generic.txt"
cp "${INDI_MANIFEST}" "${METADATA}/indi-install_manifest.txt"
cp "${INDI_3RDPARTY_MANIFEST}" "${METADATA}/indi-3rdparty-install_manifest.txt"

ARCHIVE_NAME="${ARCHIVE_NAME:-mf-pifinder-indi-bookworm-arm64-$(date +%Y%m%d-%H%M%S).tar.gz}"
ARCHIVE_PATH="${OUT_DIR}/${ARCHIVE_NAME}"

tar --owner=0 --group=0 --numeric-owner -C "${STAGING}" -czf "${ARCHIVE_PATH}" .
sha256sum "${ARCHIVE_PATH}" > "${ARCHIVE_PATH}.sha256"

echo "Created archive: ${ARCHIVE_PATH}"
echo "Checksum: ${ARCHIVE_PATH}.sha256"
