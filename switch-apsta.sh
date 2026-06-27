#! /usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -n "AP+STA" > "${SCRIPT_DIR}/wifi_status.txt"
cp /etc/dhcpcd.conf.apsta /etc/dhcpcd.conf
"${SCRIPT_DIR}/scripts/pifinder_apsta.sh" prepare
systemctl enable pifinder_apsta_prepare
systemctl enable pifinder_apsta_monitor
systemctl enable dnsmasq
systemctl enable hostapd
