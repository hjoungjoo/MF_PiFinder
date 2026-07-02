#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${1:-}"
FASTAPI_VERSION="${FASTAPI_VERSION:-0.103.2}"
STARLETTE_VERSION="${STARLETTE_VERSION:-0.27.0}"
UVICORN_VERSION="${UVICORN_VERSION:-0.23.2}"
ANYIO_VERSION="${ANYIO_VERSION:-3.7.1}"

usage() {
    echo "Usage: $0 <mf-pifinder-indi-bookworm-arm64.tar.gz>" >&2
}

if [ -z "${ARCHIVE}" ]; then
    usage
    exit 1
fi

if [ ! -f "${ARCHIVE}" ]; then
    echo "Archive not found: ${ARCHIVE}" >&2
    exit 1
fi

if [ "$(uname -m)" != "aarch64" ]; then
    echo "This archive installer supports only Raspberry Pi OS Bookworm 64-bit/aarch64." >&2
    exit 1
fi

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${VERSION_CODENAME:-}" != "bookworm" ]; then
        echo "WARNING: expected Bookworm, found ${PRETTY_NAME:-unknown OS}." >&2
    fi
fi

SERVICE_USER="${SUDO_USER:-$(whoami)}"
if [ "${SERVICE_USER}" = "root" ] && id pifinder >/dev/null 2>&1; then
    SERVICE_USER="pifinder"
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

tar -C "${TMPDIR}" -xzf "${ARCHIVE}"

if [ ! -d "${TMPDIR}/rootfs" ] || [ ! -f "${TMPDIR}/metadata/build_info.txt" ]; then
    echo "Invalid archive format: missing rootfs or metadata/build_info.txt" >&2
    exit 1
fi

echo "Installing INDI binary archive:"
cat "${TMPDIR}/metadata/build_info.txt"
echo

sudo apt update
sudo apt install -y \
    libev4 libgps28 libgsl27 libraw20 zlib1g libftdi1-2 \
    libjpeg62-turbo libkrb5-3 libnova-0.16-0 libtiff6 \
    libfftw3-double3 librtlsdr0 libcfitsio10 libgphoto2-6 \
    libusb-1.0-0 libdc1394-25 libboost-regex1.74.0 \
    libcurl3-gnutls libtheora0 liblimesuite22.09-1 \
    libavcodec59 libavdevice59 libavformat59 libavutil57 \
    libswscale6 libzmq5 libudev1 libdbus-1-3 libglib2.0-0 \
    python3-pip python3-setuptools chrony

PIP_BREAK_SYSTEM_PACKAGES=1 sudo python3 -m pip install --break-system-packages \
    jinja2 \
    "fastapi==${FASTAPI_VERSION}" \
    "starlette==${STARLETTE_VERSION}" \
    "uvicorn==${UVICORN_VERSION}" \
    "anyio==${ANYIO_VERSION}" \
    "bottle==0.12.25" \
    "psutil==6.0.0" \
    "requests==2.32.4" \
    "importlib_metadata==8.5.0"

sudo systemctl stop pifinder || true
sudo systemctl stop indiwebmanager.service || true

sudo tar -C "${TMPDIR}/rootfs" -cf - . | sudo tar -C / -xpf -
sudo ldconfig

cat > "${TMPDIR}/indiwebmanager.service" <<EOF
[Unit]
Description=INDI Web Manager
After=multi-user.target

[Service]
Type=idle
User=${SERVICE_USER}
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/local/bin/indi-web -v
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo cp "${TMPDIR}/indiwebmanager.service" /etc/systemd/system/indiwebmanager.service
sudo chown root:root /etc/systemd/system/indiwebmanager.service
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
echo "INDI archive install complete."
echo "Open INDI Web Manager at: http://pifinder.local:8624"
