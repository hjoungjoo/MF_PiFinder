#!/usr/bin/env bash
# Install or manage chronyd as the preferred PiFinder system-clock manager.

set -euo pipefail

SERVICE_NAME="chrony.service"
if systemctl list-unit-files chronyd.service >/dev/null 2>&1; then
    SERVICE_NAME="chronyd.service"
fi

install_chrony() {
    if ! command -v chronyc >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y chrony
    fi
    sudo systemctl enable --now "${SERVICE_NAME}"
    echo "chronyd is enabled as the system-clock manager."
    echo "Use PiFinder Settings > Advanced > Time Sync > Clock Manager = Chrony."
}

restart_chrony() {
    sudo systemctl restart "${SERVICE_NAME}"
}

show_status() {
    systemctl status "${SERVICE_NAME}" --no-pager || true
    if command -v chronyc >/dev/null 2>&1; then
        chronyc tracking || true
        chronyc sources -v || true
    else
        echo "chronyc is not installed."
    fi
}

case "${1:-install}" in
    install|enable)
        install_chrony
        ;;
    restart)
        restart_chrony
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {install|enable|restart|status}" >&2
        exit 2
        ;;
esac
