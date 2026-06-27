#! /usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#systemctl stop dnsmasq
#systemctl stop hostapd
systemctl disable pifinder_apsta_monitor 2>/dev/null || true
systemctl disable pifinder_apsta_prepare 2>/dev/null || true
systemctl stop pifinder_apsta_monitor 2>/dev/null || true
"${SCRIPT_DIR}/scripts/pifinder_apsta.sh" cleanup 2>/dev/null || true
cp /etc/dhcpcd.conf.sta /etc/dhcpcd.conf
systemctl disable dnsmasq
systemctl disable hostapd
#systemctl restart dhcpcd
echo -n "Client" > "${SCRIPT_DIR}/wifi_status.txt"
