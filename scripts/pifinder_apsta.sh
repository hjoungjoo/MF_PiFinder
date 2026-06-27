#!/usr/bin/env bash
# Manage PiFinder's AP+STA Wi-Fi mode.
#
# wlan0 remains the STA interface for internet access. uap0 is a virtual AP
# interface used by hostapd/dnsmasq for PiFinderAP. Broadcom single-radio
# devices require the AP to follow the associated STA channel, so monitor mode
# rewrites hostapd.conf and restarts hostapd when the STA channel changes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIFINDER_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

STA_IFACE="${PIFINDER_STA_IFACE:-wlan0}"
AP_IFACE="${PIFINDER_AP_IFACE:-uap0}"
AP_IP_CIDR="${PIFINDER_AP_IP_CIDR:-}"
DEFAULT_CHANNEL="${PIFINDER_AP_DEFAULT_CHANNEL:-7}"
CHANNEL_WAIT_SECONDS="${PIFINDER_APSTA_CHANNEL_WAIT_SECONDS:-15}"
STATUS_FILE="${PIFINDER_REPO_DIR}/wifi_status.txt"
HOSTAPD_CONF="${PIFINDER_HOSTAPD_CONF:-/etc/hostapd/hostapd.conf}"
DNSMASQ_CONF="${PIFINDER_DNSMASQ_CONF:-/etc/dnsmasq.conf}"
APSTA_DHCPCD_CONF="${PIFINDER_APSTA_DHCPCD_CONF:-/etc/dhcpcd.conf.apsta}"
APSTA_NAT_CONF="${PIFINDER_APSTA_NAT_CONF:-/etc/pifinder_apsta_nat.conf}"
NFT_TABLE="${PIFINDER_APSTA_NFT_TABLE:-pifinder_apsta}"

IW_BIN="${IW_BIN:-$(command -v iw || true)}"
IP_BIN="${IP_BIN:-$(command -v ip || true)}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-$(command -v systemctl || true)}"
SYSCTL_BIN="${SYSCTL_BIN:-$(command -v sysctl || true)}"
NFT_BIN="${NFT_BIN:-$(command -v nft || true)}"

log() {
    printf "pifinder_apsta: %s\n" "$*" >&2
}

wifi_mode() {
    if [[ -r "${STATUS_FILE}" ]]; then
        tr -d '\n' <"${STATUS_FILE}"
    else
        printf "Client"
    fi
}

is_apsta_mode() {
    [[ "$(wifi_mode)" == "AP+STA" ]]
}

ap_ip_cidr() {
    if [[ -n "${AP_IP_CIDR}" ]]; then
        printf "%s\n" "${AP_IP_CIDR}"
        return
    fi
    sed -n 's/^[[:space:]]*static ip_address=//p' "${APSTA_DHCPCD_CONF}" 2>/dev/null | head -n 1
}

require_commands() {
    if [[ -z "${IW_BIN}" || -z "${IP_BIN}" ]]; then
        log "iw and ip are required for AP+STA mode"
        exit 1
    fi
}

freq_to_channel() {
    local freq="$1"
    if [[ -z "${freq}" ]]; then
        return 1
    fi
    if [[ "${freq}" -eq 2484 ]]; then
        printf "14\n"
    elif [[ "${freq}" -ge 2412 && "${freq}" -le 2472 ]]; then
        printf "%s\n" $(((freq - 2407) / 5))
    elif [[ "${freq}" -ge 5000 && "${freq}" -le 5900 ]]; then
        printf "%s\n" $(((freq - 5000) / 5))
    else
        return 1
    fi
}

sta_channel() {
    local channel
    channel="$(sta_channel_or_empty)"
    if [[ -n "${channel}" ]]; then
        printf "%s\n" "${channel}"
        return
    fi
    printf "%s\n" "${DEFAULT_CHANNEL}"
}

sta_channel_or_empty() {
    local link_output freq channel
    link_output="$("${IW_BIN}" dev "${STA_IFACE}" link 2>/dev/null || true)"
    freq="$(awk '/freq:/ {print $2; exit}' <<<"${link_output}")"
    channel="$(freq_to_channel "${freq}" 2>/dev/null || true)"
    if [[ -n "${channel}" ]]; then
        printf "%s\n" "${channel}"
        return 0
    fi
    return 0
}

wait_sta_channel() {
    local channel elapsed=0
    while [[ "${elapsed}" -lt "${CHANNEL_WAIT_SECONDS}" ]]; do
        channel="$(sta_channel_or_empty)"
        if [[ -n "${channel}" ]]; then
            printf "%s\n" "${channel}"
            return
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    printf "%s\n" "${DEFAULT_CHANNEL}"
}

channel_hw_mode() {
    local channel="$1"
    if [[ "${channel}" -gt 14 ]]; then
        printf "a\n"
    else
        printf "g\n"
    fi
}

update_key_value_file() {
    local path="$1"
    local key="$2"
    local value="$3"
    local tmp
    tmp="$(mktemp)"
    if [[ -f "${path}" ]]; then
        awk -v key="${key}" -v value="${value}" '
            BEGIN { found = 0 }
            $0 ~ "^" key "=" {
                print key "=" value
                found = 1
                next
            }
            { print }
            END {
                if (!found) {
                    print key "=" value
                }
            }
        ' "${path}" >"${tmp}"
    else
        printf "%s=%s\n" "${key}" "${value}" >"${tmp}"
    fi
    cp "${tmp}" "${path}"
    rm -f "${tmp}"
}

configure_hostapd() {
    local interface="$1"
    local channel="$2"
    local hw_mode
    hw_mode="$(channel_hw_mode "${channel}")"
    update_key_value_file "${HOSTAPD_CONF}" "interface" "${interface}"
    update_key_value_file "${HOSTAPD_CONF}" "channel" "${channel}"
    update_key_value_file "${HOSTAPD_CONF}" "hw_mode" "${hw_mode}"
}

current_hostapd_channel() {
    awk -F= '/^channel=/ {print $2; exit}' "${HOSTAPD_CONF}" 2>/dev/null || true
}

configure_dnsmasq() {
    local interface="$1"
    update_key_value_file "${DNSMASQ_CONF}" "interface" "${interface} # Listening interface"
}

ap_iface_exists() {
    "${IW_BIN}" dev "${AP_IFACE}" info >/dev/null 2>&1
}

ensure_ap_iface() {
    require_commands
    if ap_iface_exists; then
        return
    fi
    log "creating ${AP_IFACE} on ${STA_IFACE}"
    "${IW_BIN}" dev "${STA_IFACE}" interface add "${AP_IFACE}" type __ap
}

configure_ap_ip() {
    require_commands
    local cidr
    cidr="$(ap_ip_cidr)"
    if [[ -z "${cidr}" ]]; then
        cidr="10.10.10.1/24"
    fi
    "${IP_BIN}" link set "${AP_IFACE}" up
    "${IP_BIN}" addr replace "${cidr}" dev "${AP_IFACE}"
}

internet_sharing_enabled() {
    grep -qx "PIFINDER_APSTA_SHARE_INTERNET=1" "${APSTA_NAT_CONF}" 2>/dev/null
}

sta_has_default_route() {
    "${IP_BIN}" route show default dev "${STA_IFACE}" 2>/dev/null | grep -q '^default '
}

nft_table_exists() {
    [[ -n "${NFT_BIN}" ]] && "${NFT_BIN}" list table ip "${NFT_TABLE}" >/dev/null 2>&1
}

enable_internet_sharing() {
    if [[ -z "${NFT_BIN}" || -z "${SYSCTL_BIN}" ]]; then
        log "nft and sysctl are required for AP+STA internet sharing"
        return 0
    fi
    if ! sta_has_default_route; then
        disable_internet_sharing
        return 0
    fi
    "${SYSCTL_BIN}" -w net.ipv4.ip_forward=1 >/dev/null
    if nft_table_exists; then
        return 0
    fi
    "${NFT_BIN}" add table ip "${NFT_TABLE}"
    "${NFT_BIN}" add chain ip "${NFT_TABLE}" postrouting "{ type nat hook postrouting priority srcnat; policy accept; }"
    "${NFT_BIN}" add chain ip "${NFT_TABLE}" forward "{ type filter hook forward priority filter; policy accept; }"
    "${NFT_BIN}" add rule ip "${NFT_TABLE}" postrouting oifname "${STA_IFACE}" masquerade
    "${NFT_BIN}" add rule ip "${NFT_TABLE}" forward iifname "${AP_IFACE}" oifname "${STA_IFACE}" accept
    "${NFT_BIN}" add rule ip "${NFT_TABLE}" forward iifname "${STA_IFACE}" oifname "${AP_IFACE}" ct state related,established accept
    log "AP+STA internet sharing enabled"
}

disable_internet_sharing() {
    if [[ -n "${NFT_BIN}" ]]; then
        "${NFT_BIN}" delete table ip "${NFT_TABLE}" >/dev/null 2>&1 || true
    fi
}

configure_internet_sharing() {
    if is_apsta_mode && internet_sharing_enabled; then
        enable_internet_sharing
    else
        disable_internet_sharing
    fi
}

prepare_apsta() {
    if ! is_apsta_mode; then
        log "not in AP+STA mode; skipping prepare"
        return 0
    fi
    local channel
    channel="$(wait_sta_channel)"
    ensure_ap_iface
    configure_dnsmasq "${AP_IFACE}"
    configure_hostapd "${AP_IFACE}" "${channel}"
    configure_ap_ip
    configure_internet_sharing
    log "prepared ${AP_IFACE} on channel ${channel}"
}

configure_ap_only() {
    configure_dnsmasq "${STA_IFACE}"
    configure_hostapd "${STA_IFACE}" "${DEFAULT_CHANNEL}"
}

cleanup_apsta() {
    require_commands
    disable_internet_sharing
    if ap_iface_exists; then
        "${IP_BIN}" link set "${AP_IFACE}" down 2>/dev/null || true
        "${IW_BIN}" dev "${AP_IFACE}" del 2>/dev/null || true
        log "removed ${AP_IFACE}"
    fi
}

restart_hostapd() {
    if [[ -n "${SYSTEMCTL_BIN}" ]]; then
        "${SYSTEMCTL_BIN}" restart hostapd || true
    fi
}

monitor_apsta() {
    local last_channel
    last_channel="$(current_hostapd_channel)"
    while true; do
        if ! is_apsta_mode; then
            sleep 30
            continue
        fi

        local channel
        channel="$(sta_channel)"
        ensure_ap_iface
        configure_ap_ip
        configure_internet_sharing
        if [[ "${channel}" != "${last_channel}" ]]; then
            configure_hostapd "${AP_IFACE}" "${channel}"
            restart_hostapd
            last_channel="${channel}"
            log "hostapd set to STA channel ${channel}"
        fi
        sleep 20
    done
}

case "${1:-prepare}" in
    prepare)
        prepare_apsta
        ;;
    monitor)
        monitor_apsta
        ;;
    cleanup)
        cleanup_apsta
        ;;
    configure-ap)
        disable_internet_sharing
        configure_ap_only
        ;;
    sharing)
        configure_internet_sharing
        ;;
    channel)
        sta_channel
        ;;
    *)
        echo "Usage: $0 {prepare|monitor|cleanup|configure-ap|sharing|channel}" >&2
        exit 2
        ;;
esac
