import pytest

try:
    from PiFinder import board_config
    from PiFinder import sys_utils

    @pytest.mark.unit
    def test_wpa_supplicant_parsing():
        # This could be read from a file or passed from another function
        wpa_supplicant_example = """
        ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
        update_config=1
        country=US

        network={
            ssid="My Home Network"
            psk="password123"
            key_mgmt=WPA-PSK
        }

        network={
            ssid="Work Network"
            psk="compl3x=p@ssw0rd!"
            key_mgmt=WPA-PSK
        }
        """
        wpa_list = [
            line.strip()
            for line in wpa_supplicant_example.strip().split("\n")
            if line.strip()
        ]
        result = sys_utils.Network._parse_wpa_supplicant(wpa_list)
        assert result[1]["psk"] == "compl3x=p@ssw0rd!"

        example2 = """
        ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
        update_config=1

















        network={
                ssid="testytest"
                psk="oesrucoeahu1234"
                key_mgmt=WPA-PSK
        }

        network={
                ssid="00xx33"
                psk="1234@===!!!"
                key_mgmt=WPA-PSK
        }
        """
        wpa_list = [line for line in example2.split("\n") if line.strip()]
        result = sys_utils.Network._parse_wpa_supplicant(wpa_list)
        assert result[1]["psk"] == "1234@===!!!"

    @pytest.mark.unit
    def test_populate_wifi_networks_missing_wpa_file(monkeypatch):
        real_open = open

        def fake_open(path, *args, **kwargs):
            if path == "/etc/wpa_supplicant/wpa_supplicant.conf":
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        network = sys_utils.Network.__new__(sys_utils.Network)
        network.populate_wifi_networks()

        assert network.get_wifi_networks() == []

    @pytest.mark.unit
    def test_networkmanager_connection_parsing_hex_ssid():
        nmconnection = """
        [connection]
        id=Home WiFi
        type=wifi

        [wifi]
        mode=infrastructure
        ssid=486f6d652057694669

        [wifi-security]
        key-mgmt=wpa-psk
        psk=secretpass
        """

        result = sys_utils.Network._parse_networkmanager_connection(nmconnection)

        assert result["ssid"] == "Home WiFi"
        assert result["psk"] == "secretpass"
        assert result["key_mgmt"] == "WPA-PSK"

    @pytest.mark.unit
    def test_dedupe_wifi_networks_reassigns_ids():
        networks = sys_utils.Network._dedupe_wifi_networks(
            [
                {"id": 99, "ssid": "Home", "psk": "secretpass", "key_mgmt": "WPA-PSK"},
                {"id": 1, "ssid": "Home", "psk": "otherpass", "key_mgmt": "WPA-PSK"},
                {"id": 2, "ssid": "Open", "key_mgmt": "NONE"},
            ]
        )

        assert networks == [
            {"id": 0, "ssid": "Home", "psk": "secretpass", "key_mgmt": "WPA-PSK"},
            {"id": 1, "ssid": "Open", "psk": None, "key_mgmt": "NONE"},
        ]

    @pytest.mark.unit
    def test_iw_scan_parsing_dedupes_ssids():
        output = """
        BSS 00:11:22:33:44:55(on wlan0)
                SSID: Cafe
        BSS 66:77:88:99:aa:bb(on wlan0)
                SSID: Cafe
        BSS cc:dd:ee:ff:00:11(on wlan0)
                SSID:
        BSS 22:33:44:55:66:77(on wlan0)
                SSID: Observatory
        """

        assert sys_utils.Network._parse_iw_scan(output) == ["Cafe", "Observatory"]

    @pytest.mark.unit
    def test_hostapd_security_rewrite_adds_wpa2_and_removes_pairwise():
        lines = [
            "interface=wlan0\n",
            "ssid=PiFinderAP\n",
            "channel=7\n",
            "wpa_pairwise=TKIP\n",
        ]

        result = sys_utils.Network._rewrite_key_value_lines(
            lines,
            {
                "wpa": "2",
                "wpa_passphrase": "observing",
                "wpa_key_mgmt": "WPA-PSK",
                "rsn_pairwise": "CCMP",
            },
            {"wpa_pairwise"},
        )

        assert "wpa_pairwise=TKIP\n" not in result
        assert "wpa=2\n" in result
        assert "wpa_passphrase=observing\n" in result
        assert "wpa_key_mgmt=WPA-PSK\n" in result
        assert "rsn_pairwise=CCMP\n" in result

    @pytest.mark.unit
    def test_hostapd_security_rewrite_removes_wpa_for_open_ap():
        lines = [
            "interface=wlan0\n",
            "ssid=PiFinderAP\n",
            "wpa=2\n",
            "wpa_passphrase=observing\n",
            "wpa_key_mgmt=WPA-PSK\n",
            "rsn_pairwise=CCMP\n",
        ]

        result = sys_utils.Network._rewrite_key_value_lines(
            lines,
            {},
            {"wpa", "wpa_passphrase", "wpa_key_mgmt", "rsn_pairwise"},
        )

        assert result == ["interface=wlan0\n", "ssid=PiFinderAP\n"]

    @pytest.mark.unit
    def test_ap_dhcp_range_avoids_ap_ip():
        assert sys_utils.Network._ap_dhcp_range("10.10.10.1") == (
            "10.10.10.2",
            "10.10.10.20",
        )
        start, end = sys_utils.Network._ap_dhcp_range("10.10.10.2")
        assert start == "10.10.10.1"
        assert end == "10.10.10.20"

    @pytest.mark.unit
    def test_ap_ip_validation_rejects_public_and_link_local():
        with pytest.raises(ValueError):
            sys_utils.Network._validate_ap_ip("8.8.8.8")
        with pytest.raises(ValueError):
            sys_utils.Network._validate_ap_ip("169.254.1.1")

    @pytest.mark.unit
    def test_dhcpcd_static_ip_rewrite_for_ap_interface():
        contents = "interface wlan0\n    static ip_address=10.10.10.1/24\n"
        result = sys_utils.Network._rewrite_dhcpcd_static_ip(
            contents, "wlan0", "192.168.50.1"
        )

        assert "interface wlan0\n" in result
        assert "static ip_address=192.168.50.1/24" in result
        assert "10.10.10.1" not in result

    @pytest.mark.unit
    def test_dnsmasq_ap_network_rewrite():
        contents = (
            "interface=uap0 # Listening interface\n"
            "dhcp-range=10.10.10.2,10.10.10.20,255.255.255.0,24h\n"
            "address=/gw.wlan/10.10.10.1\n"
        )
        result = sys_utils.Network._rewrite_dnsmasq_ap_network(
            contents, "192.168.50.1"
        )

        assert "interface=uap0 # Listening interface\n" in result
        assert "dhcp-range=192.168.50.2,192.168.50.20,255.255.255.0,24h\n" in result
        assert "address=/gw.wlan/192.168.50.1\n" in result

    @pytest.mark.unit
    def test_apsta_nat_config_parse_defaults_off():
        assert not sys_utils.Network._parse_apsta_nat_config("")
        assert not sys_utils.Network._parse_apsta_nat_config(
            "PIFINDER_APSTA_SHARE_INTERNET=0\n"
        )
        assert sys_utils.Network._parse_apsta_nat_config(
            "PIFINDER_APSTA_SHARE_INTERNET=1\n"
        )

    @pytest.mark.unit
    def test_sta_band_preference_rewrites_scan_freq():
        contents = (
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "\nnetwork={\n"
            '\tssid="DualBand"\n'
            '\tpsk="secretpass"\n'
            "\tkey_mgmt=WPA-PSK\n"
            "}\n"
        )

        result = sys_utils.Network._rewrite_wpa_supplicant_band_preference(
            contents, "2.4"
        )

        assert "scan_freq=2412 2417" in result
        assert "ssid=\"DualBand\"" in result

    @pytest.mark.unit
    def test_sta_band_auto_removes_scan_freq():
        contents = (
            "network={\n"
            '\tssid="DualBand"\n'
            "\tscan_freq=2412 2417 2422\n"
            "\tkey_mgmt=NONE\n"
            "}\n"
        )

        result = sys_utils.Network._rewrite_wpa_supplicant_band_preference(
            contents, "auto"
        )

        assert "scan_freq=" not in result
        assert "key_mgmt=NONE" in result

    @pytest.mark.unit
    def test_sta_band_preference_normalization():
        assert sys_utils.Network._normalize_sta_band_preference("2.4GHz") == "2.4"
        assert sys_utils.Network._normalize_sta_band_preference("5g") == "5"
        assert sys_utils.Network._normalize_sta_band_preference("") == "auto"
        with pytest.raises(ValueError):
            sys_utils.Network._normalize_sta_band_preference("6")

    @pytest.mark.unit
    def test_dnsmasq_lease_parsing():
        leases = sys_utils.Network._parse_dnsmasq_leases(
            "1782816834 06:43:af:65:75:9b 10.10.10.15 phone 01:06:43\n"
            "1782815524 da:4f:d2:62:87:74 10.10.10.14 * 01:da:4f\n"
        )

        assert leases["06:43:af:65:75:9b"]["hostname"] == "phone"
        assert leases["da:4f:d2:62:87:74"]["hostname"] == ""
        assert leases["da:4f:d2:62:87:74"]["ip"] == "10.10.10.14"

    @pytest.mark.unit
    def test_iw_station_dump_parsing():
        output = """
        Station da:4f:d2:62:87:74 (on uap0)
            inactive time:  20 ms
            rx bitrate:     72.2 MBit/s
            tx bitrate:     65.0 MBit/s
            authorized:     yes
        """

        stations = sys_utils.Network._parse_iw_station_dump(output)

        assert stations["da:4f:d2:62:87:74"]["connected"]
        assert stations["da:4f:d2:62:87:74"]["rx_bitrate"] == "72.2 MBit/s"
        assert stations["da:4f:d2:62:87:74"]["tx_bitrate"] == "65.0 MBit/s"

    @pytest.mark.unit
    def test_ip_neighbor_parsing_by_mac_and_failed_ip():
        neighbors = sys_utils.Network._parse_ip_neigh(
            "10.10.10.15 lladdr 06:43:af:65:75:9b REACHABLE\n"
            "10.10.10.14 FAILED\n"
        )

        assert neighbors["06:43:af:65:75:9b"]["neighbor_state"] == "REACHABLE"
        assert neighbors["10.10.10.14"]["neighbor_state"] == "FAILED"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("model", "profile", "gps_device", "uart_overlay"),
        [
            (
                "Raspberry Pi 5 Model B Rev 1.0",
                "pi5_class",
                "/dev/ttyAMA2",
                "dtoverlay=uart2-pi5",
            ),
            (
                "Raspberry Pi Compute Module 5 Rev 1.0",
                "pi5_class",
                "/dev/ttyAMA2",
                "dtoverlay=uart2-pi5",
            ),
            (
                "Raspberry Pi 4 Model B Rev 1.5",
                "pi4",
                "/dev/ttyAMA3",
                "dtoverlay=uart3",
            ),
            (
                "Raspberry Pi 3 Model B Plus Rev 1.3",
                "legacy",
                "/dev/ttyAMA1",
                "dtoverlay=uart3",
            ),
        ],
    )
    def test_board_profile_by_model(model, profile, gps_device, uart_overlay):
        board_profile = board_config.get_board_profile(model)

        assert board_profile.name == profile
        assert board_profile.gps_device == gps_device
        assert board_profile.uart_overlay == uart_overlay

    @pytest.mark.unit
    def test_resolve_gpsd_device_uses_board_default(monkeypatch):
        monkeypatch.setattr(sys_utils, "get_default_gpsd_device", lambda: "/dev/ttyAMA3")

        assert sys_utils.resolve_gpsd_device(None) == "/dev/ttyAMA3"
        assert sys_utils.resolve_gpsd_device("auto") == "/dev/ttyAMA3"
        assert sys_utils.resolve_gpsd_device("/dev/ttyACM0") == "/dev/ttyACM0"

    @pytest.mark.unit
    def test_rewrite_hosts_standard_line():
        contents = (
            "127.0.0.1\tlocalhost\n"
            "::1\t\tlocalhost ip6-localhost ip6-loopback\n"
            "127.0.1.1\tpifinder\n"
        )
        result = sys_utils.Network._rewrite_hosts(contents, "pf-rich")
        assert "127.0.1.1\tpf-rich\n" in result
        assert "pifinder" not in result
        assert "127.0.0.1\tlocalhost\n" in result

    @pytest.mark.unit
    def test_rewrite_hosts_preserves_aliases_and_spacing():
        contents = "  127.0.1.1   pifinder pifinder.local  # primary\n"
        result = sys_utils.Network._rewrite_hosts(contents, "pf-rich")
        assert result == "  127.0.1.1   pf-rich pifinder.local  # primary\n"

    @pytest.mark.unit
    def test_rewrite_hosts_appends_when_missing():
        contents = "127.0.0.1\tlocalhost\n"
        result = sys_utils.Network._rewrite_hosts(contents, "pf-rich")
        assert result.endswith("127.0.1.1\tpf-rich\n")
        assert "127.0.0.1\tlocalhost\n" in result

    @pytest.mark.unit
    def test_rewrite_hosts_appends_with_missing_trailing_newline():
        contents = "127.0.0.1\tlocalhost"
        result = sys_utils.Network._rewrite_hosts(contents, "pf-rich")
        assert result == "127.0.0.1\tlocalhost\n127.0.1.1\tpf-rich\n"

    @pytest.mark.unit
    def test_rewrite_hosts_ignores_commented_line():
        contents = "# 127.0.1.1 oldname\n127.0.0.1\tlocalhost\n"
        result = sys_utils.Network._rewrite_hosts(contents, "pf-rich")
        # commented line is untouched; a real 127.0.1.1 entry is appended
        assert "# 127.0.1.1 oldname\n" in result
        assert result.endswith("127.0.1.1\tpf-rich\n")


except ImportError:
    pass
