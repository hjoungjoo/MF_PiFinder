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

sudo apt-get install -y git python3-pip samba samba-common-bin dnsmasq hostapd dhcpd gpsd

if [[ -d PiFinder/ ]]; then
    cd PiFinder/ && git config pull.rebase false && git pull
else
    git clone --recursive --branch release https://github.com/brickbots/PiFinder.git
fi

PIFINDER_REPO_DIR="${PIFINDER_HOME}/PiFinder"
source "${PIFINDER_REPO_DIR}/pifinder_paths.sh"

cd "${PIFINDER_REPO_DIR}"
sudo python3 -m pip install --break-system-packages -r python/requirements.txt

# Setup GPSD
sudo dpkg-reconfigure -plow gpsd
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/gpsd.conf" /etc/default/gpsd

# data dirs
mkdir -p \
    "${PIFINDER_DATA_DIR}" \
    "${PIFINDER_DATA_DIR}/captures" \
    "${PIFINDER_DATA_DIR}/obslists" \
    "${PIFINDER_DATA_DIR}/screenshots" \
    "${PIFINDER_DATA_DIR}/solver_debug_dumps" \
    "${PIFINDER_DATA_DIR}/logs" \
    "${PIFINDER_DATA_DIR}/migrations"
find "${PIFINDER_DATA_DIR}" -type d -exec chmod 755 {} \;

# Wifi config
sudo cp "${PIFINDER_REPO_DIR}"/pi_config_files/dhcpcd.* /etc
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/dhcpcd.conf.sta" /etc/dhcpcd.conf
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/dnsmasq.conf" /etc/dnsmasq.conf
sudo cp "${PIFINDER_REPO_DIR}/pi_config_files/hostapd.conf" /etc/hostapd/hostapd.conf
echo -n "Client" > "${PIFINDER_REPO_DIR}/wifi_status.txt"
sudo systemctl unmask hostapd

# open permissisons on wpa_supplicant file so we can adjust network config
sudo chmod 666 /etc/wpa_supplicant/wpa_supplicant.conf

# Samba config
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/smb.conf" /etc/samba/smb.conf

# Hipparcos catalog
HIP_MAIN_DAT="${PIFINDER_REPO_DIR}/astro_data/hip_main.dat"
if [[ ! -e $HIP_MAIN_DAT ]]; then
    wget -O $HIP_MAIN_DAT https://cdsarc.cds.unistra.fr/ftp/cats/I/239/hip_main.dat
fi

# Enable interfaces
BOOT_CONFIG="$(pifinder_boot_config_path)"
grep -q "dtparam=spi=on" "${BOOT_CONFIG}" || \
   echo "dtparam=spi=on" | sudo tee -a "${BOOT_CONFIG}"
grep -q "dtparam=i2c_arm=on" "${BOOT_CONFIG}" || \
   echo "dtparam=i2c_arm=on" | sudo tee -a "${BOOT_CONFIG}"
grep -q "dtparam=i2c_arm_baudrate=10000" "${BOOT_CONFIG}" || \
   echo "dtparam=i2c_arm_baudrate=10000" | sudo tee -a "${BOOT_CONFIG}"
grep -q "dtoverlay=pwm,pin=13,func=4" "${BOOT_CONFIG}" || \
   echo "dtoverlay=pwm,pin=13,func=4" | sudo tee -a "${BOOT_CONFIG}"
grep -q "dtoverlay=uart3" "${BOOT_CONFIG}" || \
   echo "dtoverlay=uart3" | sudo tee -a "${BOOT_CONFIG}"
# Note: camera types are added lateron by python/PiFinder/switch_camera.py

# Disable unwanted services
sudo systemctl disable ModemManager

# Enable service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder.service" /lib/systemd/system/pifinder.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/pifinder_splash.service" /lib/systemd/system/pifinder_splash.service
pifinder_render_config "${PIFINDER_REPO_DIR}/pi_config_files/cedar_detect.service" /lib/systemd/system/cedar_detect.service
sudo systemctl daemon-reload
sudo systemctl enable cedar_detect
sudo systemctl enable pifinder
sudo systemctl enable pifinder_splash

for group in input video render dialout gpio i2c spi; do
    if getent group "${group}" >/dev/null; then
        sudo usermod -aG "${group}" "${PIFINDER_USER}"
    fi
done

echo "PiFinder setup complete, please restart the Pi"
