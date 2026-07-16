#!/usr/bin/env bash
set -euo pipefail

# PiFinder LX200 OnStepX INDI mount-control installer.
#
# Builds INDI core + 3rd-party drivers from source with the bundled PiFinder
# INDI patch set (LX200 OnStepX Backlash 0..3600 and GUIDE_RATE readback
# fixes), then installs pyindi-client and the INDI Web Manager service.
#
# Binaries are always built with -march=armv8-a (any native/Pi 5-specific CPU
# flags are stripped from the environment) so the same build runs on both
# Raspberry Pi 4 and Pi 5.  This is also what package_indi_mount_archive.sh
# verifies when creating the redistributable archive from this build tree.
#
# All versions/paths can be overridden via environment variables, e.g.:
#   JOBS=4 bash scripts/install_indi_mount_OnstepX.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

sanitize_arm64_flags() {
    local flags="${1:-}"
    flags="$(printf '%s\n' "${flags}" | sed -E 's/(^|[[:space:]])-m(arch|cpu|tune)=[^[:space:]]+//g')"
    printf '%s' "${flags}"
}

INDI_VERSION="${INDI_VERSION:-v2.2.3.1}"
INDI_3RDPARTY_VERSION="${INDI_3RDPARTY_VERSION:-v2.2.3.1}"
PYINDI_VERSION="${PYINDI_VERSION:-v2.1.2}"
FASTAPI_VERSION="${FASTAPI_VERSION:-0.103.2}"
STARLETTE_VERSION="${STARLETTE_VERSION:-0.27.0}"
UVICORN_VERSION="${UVICORN_VERSION:-0.23.2}"
ANYIO_VERSION="${ANYIO_VERSION:-3.7.1}"
JOBS="${JOBS:-2}"
BUILD_ROOT="${BUILD_ROOT:-$HOME/indi-latest}"
INDI_PATCH_DIR="${INDI_PATCH_DIR:-${SCRIPT_DIR}/patches}"
export CFLAGS="$(sanitize_arm64_flags "${CFLAGS:-}") -march=armv8-a"
export CXXFLAGS="$(sanitize_arm64_flags "${CXXFLAGS:-}") -march=armv8-a -Wno-error=stringop-overread -Wno-error=stringop-truncation"

DISABLE_CAMERA_DRIVER_OPTIONS=(
    -DWITH_WEBCAM=OFF
    -DWITH_SX=OFF
    -DWITH_MI=OFF
    -DWITH_FLI=OFF
    -DWITH_SBIG=OFF
    -DWITH_INOVAPLX=OFF
    -DWITH_APOGEE=OFF
    -DWITH_FFMV=OFF
    -DWITH_QHY=OFF
    -DWITH_GPHOTO=OFF
    -DWITH_QSI=OFF
    -DWITH_FISHCAMP=OFF
    -DWITH_DSI=OFF
    -DWITH_ASICAM=OFF
    -DWITH_GIGE=OFF
    -DWITH_NIGHTSCAPE=OFF
    -DWITH_ATIK=OFF
    -DWITH_TOUPCAM=OFF
    -DWITH_ALTAIRCAM=OFF
    -DWITH_BRESSERCAM=OFF
    -DWITH_MALLINCAM=OFF
    -DWITH_MEADECAM=OFF
    -DWITH_NNCAM=OFF
    -DWITH_OGMACAM=OFF
    -DWITH_OPENOGMA=OFF
    -DWITH_OMEGONPROCAM=OFF
    -DWITH_STARSHOOTG=OFF
    -DWITH_TSCAM=OFF
    -DWITH_SVBONYCAM=OFF
    -DWITH_PENTAX=OFF
    -DWITH_ORION_SSG3=OFF
    -DWITH_SVBONY=OFF
    -DWITH_PLAYERONE=OFF
    -DWITH_MGEN=OFF
    -DWITH_ASTROASIS=OFF
)

cmake_install_if_available() {
    local target_list
    target_list="$(mktemp)"
    cmake --build . --target help >"${target_list}"
    if grep -Eq '(^|[.][.][.] )install($|[[:space:]])' "${target_list}"; then
        rm -f "${target_list}"
        sudo cmake --build . --target install
    else
        rm -f "${target_list}"
        echo "No CMake install target in $(pwd); skipping install."
    fi
}

apply_indi_patches() {
    local repo_dir="$1"
    local patch_dir="$2"
    local version_key="${INDI_VERSION#v}"
    local version_name="${INDI_VERSION}"
    local patch_file
    local patches=()

    if [ -z "${patch_dir}" ] || [ "${patch_dir}" = "none" ]; then
        return 0
    fi

    if [ ! -d "${patch_dir}" ]; then
        echo "ERROR: INDI_PATCH_DIR does not exist: ${patch_dir}" >&2
        exit 1
    fi

    shopt -s nullglob
    patches=("${patch_dir}"/indi-"${version_name}"-*.patch)
    if [ "${version_name}" != "${version_key}" ]; then
        patches+=("${patch_dir}"/indi-"${version_key}"-*.patch)
    fi
    shopt -u nullglob

    if [ "${#patches[@]}" -eq 0 ]; then
        echo "No PiFinder INDI patches for ${INDI_VERSION} in ${patch_dir}; continuing."
        return 0
    fi

    for patch_file in "${patches[@]}"; do
        echo "Applying PiFinder INDI patch: ${patch_file}"
        if git -C "${repo_dir}" apply --check "${patch_file}"; then
            git -C "${repo_dir}" apply "${patch_file}"
        elif git -C "${repo_dir}" apply --reverse --check "${patch_file}"; then
            echo "Patch already applied: ${patch_file}"
        else
            echo "ERROR: INDI patch cannot be applied cleanly: ${patch_file}" >&2
            exit 1
        fi
    done
}

echo "PiFinder LX200 OnStepX INDI mount-control installer"
echo "Using BUILD_ROOT=${BUILD_ROOT}"
echo "INDI_VERSION=${INDI_VERSION}"
echo "INDI_3RDPARTY_VERSION=${INDI_3RDPARTY_VERSION}"
echo "PYINDI_VERSION=${PYINDI_VERSION}"
echo "INDI_PATCH_DIR=${INDI_PATCH_DIR}"
echo "CFLAGS=${CFLAGS}"
echo "CXXFLAGS=${CXXFLAGS}"
echo "OnStepX patch includes Backlash 0..3600 and GUIDE_RATE readback handling."
echo

sudo apt update
sudo apt install -y \
    build-essential cmake git swig pkg-config meson ninja-build \
    cdbs dkms fxload libev-dev libgps-dev libgsl-dev libraw-dev \
    libusb-dev zlib1g-dev libftdi-dev libftdi1-dev libjpeg-dev \
    libkrb5-dev libnova-dev libtiff-dev libfftw3-dev librtlsdr-dev \
    libcfitsio-dev libgphoto2-dev libusb-1.0-0-dev libdc1394-dev \
    libboost-dev libboost-regex-dev libcurl4-gnutls-dev libtheora-dev \
    liblimesuite-dev libavcodec-dev libavdevice-dev libzmq3-dev \
    libudev-dev libdbus-1-dev libglib2.0-dev python3-pip \
    python3-setuptools python-dev-is-python3 chrony

sudo systemctl stop pifinder || true

PIP_BREAK_SYSTEM_PACKAGES=1 sudo python3 -m pip install --break-system-packages \
    jinja2 \
    "fastapi==${FASTAPI_VERSION}" \
    "starlette==${STARLETTE_VERSION}" \
    "uvicorn==${UVICORN_VERSION}" \
    "anyio==${ANYIO_VERSION}"

mkdir -p "${BUILD_ROOT}"
cd "${BUILD_ROOT}"
if [ ! -d indi/.git ]; then
    rm -rf indi
    git clone --branch "${INDI_VERSION}" --depth 1 https://github.com/indilib/indi.git
fi
apply_indi_patches "${BUILD_ROOT}/indi" "${INDI_PATCH_DIR}"

mkdir -p indi/build
cd indi/build
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
    "${DISABLE_CAMERA_DRIVER_OPTIONS[@]}" \
    ..
make -j"${JOBS}"
cmake_install_if_available

PIP_BREAK_SYSTEM_PACKAGES=1 sudo python3 -m pip install --break-system-packages \
    "git+https://github.com/indilib/pyindi-client.git@${PYINDI_VERSION}#egg=pyindi-client"

cd "${BUILD_ROOT}"
if [ ! -d indi-3rdparty/.git ]; then
    rm -rf indi-3rdparty
    git clone --branch "${INDI_3RDPARTY_VERSION}" --depth 1 https://github.com/indilib/indi-3rdparty.git
fi

mkdir -p indi-3rdparty/build-libs
cd indi-3rdparty/build-libs
cmake -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release -DBUILD_LIBS=1 \
    "${DISABLE_CAMERA_DRIVER_OPTIONS[@]}" \
    ..
make -j"${JOBS}"
cmake_install_if_available

mkdir -p ../build-drivers
cd ../build-drivers
cmake -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_SHARED_LINKER_FLAGS="-ludev" \
    "${DISABLE_CAMERA_DRIVER_OPTIONS[@]}" \
    ..
make -j"${JOBS}"
cmake_install_if_available

PIP_BREAK_SYSTEM_PACKAGES=1 sudo python3 -m pip install --break-system-packages \
    "git+https://github.com/jscheidtmann/indiwebmanager.git@control_panel#egg=indiweb"

# Run the INDI Web Manager as the invoking OS user, not root -- mirror the
# SERVICE_USER fallback used by install_indi_mount_archive.sh so running this
# script via sudo does not register the service with User=root.
CURRENT_USER="${SUDO_USER:-$(whoami)}"
if [ "${CURRENT_USER}" = "root" ] && id pifinder >/dev/null 2>&1; then
    CURRENT_USER="pifinder"
fi
cat >/tmp/indiwebmanager.service <<EOF
[Unit]
Description=INDI Web Manager
After=multi-user.target

[Service]
Type=idle
User=${CURRENT_USER}
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/local/bin/indi-web -v
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo cp /tmp/indiwebmanager.service /etc/systemd/system/indiwebmanager.service
sudo chmod 644 /etc/systemd/system/indiwebmanager.service
sudo systemctl daemon-reload
sudo systemctl enable indiwebmanager.service
sudo systemctl restart indiwebmanager.service

if ! sudo grep -q "refclock SHM 0 poll 3 refid gps1" /etc/chrony/chrony.conf; then
    echo "" | sudo tee -a /etc/chrony/chrony.conf >/dev/null
    echo "# Sync time from GPSD" | sudo tee -a /etc/chrony/chrony.conf >/dev/null
    echo "refclock SHM 0 poll 3 refid gps1" | sudo tee -a /etc/chrony/chrony.conf >/dev/null
fi
sudo systemctl restart chrony
sudo systemctl start pifinder || true

echo
echo "INDI mount-control install complete."
echo "Open INDI Web Manager at: http://pifinder.local:8624"
echo "Enable PiFinder mount control from Settings > Experimental > Mount Control."
