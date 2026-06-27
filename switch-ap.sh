#! /usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
systemctl disable pifinder_apsta_monitor 2>/dev/null || true
systemctl disable pifinder_apsta_prepare 2>/dev/null || true
systemctl stop pifinder_apsta_monitor 2>/dev/null || true
"${SCRIPT_DIR}/scripts/pifinder_apsta.sh" cleanup 2>/dev/null || true
"${SCRIPT_DIR}/scripts/pifinder_apsta.sh" configure-ap
cp /etc/dhcpcd.conf.ap /etc/dhcpcd.conf
systemctl enable dnsmasq
systemctl enable hostapd
echo -n "AP" > "${SCRIPT_DIR}/wifi_status.txt"
#systemctl start dnsmasq
#systemctl start hostapd
#systemctl restart dhcpcd
