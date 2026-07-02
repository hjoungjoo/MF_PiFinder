#!/usr/bin/bash
# This script installs the PiFinder software on a prepared Raspberry Pi OS.
# See https://pifinder.readthedocs.io/en/release/software.html for more info.

set -e

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Do not run this script with sudo." >&2
    echo "Run it as the target OS user; the script will use sudo when needed." >&2
    exit 1
fi

PIFINDER_USER="${PIFINDER_USER:-${SUDO_USER:-$(id -un)}}"
if [[ "${PIFINDER_USER}" == "root" ]]; then
    echo "Run as the target OS user, or set PIFINDER_USER=<user>." >&2
    exit 1
fi

PIFINDER_HOME="$(getent passwd "${PIFINDER_USER}" | cut -d: -f6)"
if [[ -z "${PIFINDER_HOME}" || ! -d "${PIFINDER_HOME}" ]]; then
    echo "Could not determine home directory for ${PIFINDER_USER}" >&2
    exit 1
fi

cd "${PIFINDER_HOME}"

sudo bash -c '
set -e
trap "rm -f /usr/sbin/policy-rc.d" EXIT
printf "%s\n" "#!/bin/sh" "exit 101" > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-pip python3-venv python3-dev build-essential pkg-config \
    samba samba-common-bin dnsmasq hostapd dhcpcd gpsd wget iw nftables \
    libinput10 libcap2-bin libjpeg-dev zlib1g-dev libfreetype6-dev \
    liblcms2-dev libopenjp2-7-dev libtiff-dev libffi-dev libssl-dev \
    python3-picamera2 rpicam-apps i2c-tools spi-tools
'

if [[ -d PiFinder/ ]]; then
    cd PiFinder/ && git config pull.rebase false && git pull
else
    git clone --recursive --branch release https://github.com/brickbots/PiFinder.git
fi

PIFINDER_REPO_DIR="${PIFINDER_HOME}/PiFinder"
source "${PIFINDER_REPO_DIR}/pifinder_paths.sh"

cd "${PIFINDER_REPO_DIR}"

find_pifinder_indi_archive() {
    local archives=()
    shopt -s nullglob
    archives=("${PIFINDER_REPO_DIR}"/dist/mf-pifinder-indi-bookworm-arm64-*.tar.gz)
    shopt -u nullglob

    if [[ "${#archives[@]}" -gt 0 ]]; then
        printf "%s\n" "${archives[@]}" | sort | tail -n 1
    fi
}

install_optional_indi_archive() {
    local mode="${PIFINDER_INSTALL_INDI_ARCHIVE:-auto}"
    local archive="${PIFINDER_INDI_ARCHIVE:-}"

    mode="${mode,,}"
    if [[ -z "${archive}" ]]; then
        archive="$(find_pifinder_indi_archive || true)"
    fi

    case "${mode}" in
        1|true|yes|on|archive)
            if [[ -z "${archive}" ]]; then
                echo "PIFINDER_INSTALL_INDI_ARCHIVE is enabled, but no INDI archive was found." >&2
                echo "Set PIFINDER_INDI_ARCHIVE=/path/to/mf-pifinder-indi-bookworm-arm64.tar.gz." >&2
                exit 1
            fi
            ;;
        auto|"")
            if [[ -z "${archive}" ]]; then
                echo "No INDI binary archive found; skipping optional INDI mount support."
                echo "To install it during setup, put the archive in ${PIFINDER_REPO_DIR}/dist or set PIFINDER_INDI_ARCHIVE."
                return 0
            fi
            ;;
        0|false|no|off|none|skip)
            echo "Skipping optional INDI mount support."
            return 0
            ;;
        *)
            echo "Invalid PIFINDER_INSTALL_INDI_ARCHIVE value: ${mode}" >&2
            echo "Use auto, true, or false." >&2
            exit 1
            ;;
    esac

    if [[ ! -f "${archive}" ]]; then
        echo "INDI archive not found: ${archive}" >&2
        exit 1
    fi

    echo "Installing optional INDI mount support from ${archive}"
    bash "${PIFINDER_REPO_DIR}/scripts/install_indi_mount_archive.sh" "${archive}"
}

sudo python3 -m pip install --break-system-packages -r python/requirements.txt

# Setup GPSD
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/gpsd.conf" /etc/default/gpsd
sudo sed -i "s|^DEVICES=.*|DEVICES=\"$(pifinder_gps_device)\"|" /etc/default/gpsd

# data dirs
sudo install -d -o "${PIFINDER_USER}" -g "${PIFINDER_USER}" -m 755 \
    "${PIFINDER_DATA_DIR}" \
    "${PIFINDER_DATA_DIR}/captures" \
    "${PIFINDER_DATA_DIR}/obslists" \
    "${PIFINDER_DATA_DIR}/screenshots" \
    "${PIFINDER_DATA_DIR}/solver_debug_dumps" \
    "${PIFINDER_DATA_DIR}/logs" \
    "${PIFINDER_DATA_DIR}/migrations"

# Wifi config
sudo cp "${PIFINDER_REPO_DIR}"/pi_config_files/dhcpcd.* /etc
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/dhcpcd.conf.sta" /etc/dhcpcd.conf
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/dnsmasq.conf" /etc/dnsmasq.conf
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/hostapd.conf" /etc/hostapd/hostapd.conf
echo -n "Client" > "${PIFINDER_REPO_DIR}/wifi_status.txt"
sudo systemctl unmask hostapd

# allow the PiFinder service user to adjust network config
pifinder_prepare_wpa_supplicant_config
pifinder_prepare_apsta_nat_config
pifinder_prepare_sta_band_config
sudo python3 "${PIFINDER_REPO_DIR}/scripts/import_initial_wifi_networks.py"

# Bluetooth HID keyboards
if [[ -f /etc/bluetooth/input.conf ]]; then
    sudo sed -i \
        -e 's/^#\?UserspaceHID=.*/UserspaceHID=true/' \
        -e 's/^#\?LEAutoSecurity=.*/LEAutoSecurity=true/' \
        /etc/bluetooth/input.conf
fi

# Samba config
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/smb.conf" /etc/samba/smb.conf

# Hipparcos catalog
HIP_MAIN_DAT="${PIFINDER_REPO_DIR}/astro_data/hip_main.dat"
if [[ ! -e $HIP_MAIN_DAT ]]; then
    wget -O $HIP_MAIN_DAT https://cdsarc.cds.unistra.fr/ftp/cats/I/239/hip_main.dat
fi

# Enable interfaces
BOOT_CONFIG="$(pifinder_boot_config_path)"
for line in \
    "dtparam=spi=on" \
    "dtparam=i2c_arm=on" \
    "dtparam=i2c_arm_baudrate=10000" \
    "dtoverlay=pwm,pin=13,func=4" \
    "$(pifinder_uart_overlay)"
do
    grep -qxF "${line}" "${BOOT_CONFIG}" || echo "${line}" | sudo tee -a "${BOOT_CONFIG}"
done
if [[ "$(pifinder_uart_overlay)" == "dtoverlay=uart2-pi5" ]]; then
    sudo sed -i 's/^dtoverlay=uart3/#dtoverlay=uart3/' "${BOOT_CONFIG}"
fi
# Note: camera types are added lateron by python/PiFinder/switch_camera.py

# Disable unwanted services
sudo systemctl disable ModemManager 2>/dev/null || true
sudo systemctl disable dhcpcd dnsmasq hostapd 2>/dev/null || true

# Enable service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder.service" /lib/systemd/system/pifinder.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder_splash.service" /lib/systemd/system/pifinder_splash.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/cedar_detect.service" /lib/systemd/system/cedar_detect.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder_apsta_prepare.service" /lib/systemd/system/pifinder_apsta_prepare.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder_apsta_monitor.service" /lib/systemd/system/pifinder_apsta_monitor.service
sudo systemctl daemon-reload
sudo systemctl enable cedar_detect
sudo systemctl enable pifinder
sudo systemctl enable pifinder_splash

for group in input video render dialout gpio i2c spi; do
    if getent group "${group}" >/dev/null; then
        sudo usermod -aG "${group}" "${PIFINDER_USER}"
    fi
done

install_optional_indi_archive

echo "PiFinder setup complete, please restart the Pi"
