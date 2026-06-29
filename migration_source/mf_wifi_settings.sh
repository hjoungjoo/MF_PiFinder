echo "Installing Wi-Fi settings support"

pifinder_prepare_wpa_supplicant_config
pifinder_prepare_apsta_nat_config
pifinder_prepare_sta_band_config
sudo python3 "${PIFINDER_REPO_DIR}/scripts/import_initial_wifi_networks.py"
