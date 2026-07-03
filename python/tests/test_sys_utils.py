import pytest

try:
    from PiFinder import board_config
    from PiFinder import sys_utils

    @pytest.mark.unit
    def test_build_indi_location_time_properties_uses_input_time_and_offset():
        properties = sys_utils.build_indi_location_time_properties(
            latitude=37.52704,
            longitude=127.10936,
            elevation=30,
            utc_datetime="2026-06-30T13:45:12",
            utc_offset_hours=9,
        )

        device = sys_utils.DEFAULT_ONSTEP_DEVICE_NAME
        assert f"{device}.GEOGRAPHIC_COORD.LAT=37.52704" in properties
        assert f"{device}.GEOGRAPHIC_COORD.LONG=127.10936" in properties
        assert f"{device}.GEOGRAPHIC_COORD.ELEV=30.0" in properties
        assert f"{device}.TIME_UTC.UTC=2026-06-30T13:45:12" in properties
        assert f"{device}.TIME_UTC.OFFSET=9.00" in properties

    @pytest.mark.unit
    def test_build_indi_location_time_properties_converts_west_longitude():
        properties = sys_utils.build_indi_location_time_properties(
            latitude=34,
            longitude=-118.25,
            utc_datetime="2026-06-30T13:45:12Z",
            utc_offset_hours=-7,
        )

        device = sys_utils.DEFAULT_ONSTEP_DEVICE_NAME
        assert f"{device}.GEOGRAPHIC_COORD.LONG=241.75" in properties
        assert f"{device}.TIME_UTC.OFFSET=-7.00" in properties

    @pytest.mark.unit
    def test_format_onstep_location_display_matches_onstep_web_sign():
        degree = "\N{DEGREE SIGN}"
        assert (
            sys_utils.format_onstep_location_display(
                37.53333333333333,
                127.11666666666666,
                0,
            )
            == f"+37{degree}32'00\", -127{degree}07'00\" / 0m"
        )

    @pytest.mark.unit
    def test_onstep_location_display_uses_cached_seconds_for_coarse_indi_readback():
        degree = "\N{DEGREE SIGN}"
        onstep_props = {
            "LX200 OnStep.GEOGRAPHIC_COORD.LAT": "37.51666666666665719",
            "LX200 OnStep.GEOGRAPHIC_COORD.LONG": "127.0999999999999432",
            "LX200 OnStep.GEOGRAPHIC_COORD.ELEV": "0",
        }
        cache = {
            "latitude": 37.52704,
            "longitude": 127.10936,
            "elevation": 30,
        }

        assert sys_utils.onstep_location_readback_matches(
            onstep_props["LX200 OnStep.GEOGRAPHIC_COORD.LAT"],
            onstep_props["LX200 OnStep.GEOGRAPHIC_COORD.LONG"],
            cache["latitude"],
            cache["longitude"],
        )
        assert (
            sys_utils.format_onstep_location_display_with_cache(onstep_props, cache)
            == f"+37{degree}31'37\", -127{degree}06'34\" / 30m"
        )

    @pytest.mark.unit
    def test_parse_onstep_home_park_state_splits_at_home_from_parked():
        state = sys_utils.parse_onstep_home_park_state(
            status_text="At Home and UnParked",
            park_switch="Off",
            unpark_switch="On",
            raw_status="nNpHAo160",
        )

        assert state["home_state"] == "At Home"
        assert state["park_state"] == "Unparked"
        assert state["driver_status"] == "At Home and UnParked"
        assert state["raw_status"] == "nNpHAo160"

    @pytest.mark.unit
    def test_parse_onstep_home_park_state_uses_switch_fallback():
        state = sys_utils.parse_onstep_home_park_state(
            status_text="",
            park_switch="On",
            unpark_switch="Off",
        )

        assert state["home_state"] == "Unknown"
        assert state["park_state"] == "Parked"

    @pytest.mark.unit
    def test_onstep_location_display_uses_cached_elevation_for_exclusive_sync():
        degree = "\N{DEGREE SIGN}"
        onstep_props = {
            "LX200 OnStep.GEOGRAPHIC_COORD.LAT": "37.31666666666669983",
            "LX200 OnStep.GEOGRAPHIC_COORD.LONG": "126.8166666666666288",
            "LX200 OnStep.GEOGRAPHIC_COORD.ELEV": "0",
        }
        cache = {
            "latitude": 37.32361,
            "longitude": 126.82194,
            "elevation": 15,
        }

        assert (
            sys_utils.format_onstep_location_display_with_cache(onstep_props, cache)
            == f"+37{degree}19'25\", -126{degree}49'19\" / 15m"
        )

    @pytest.mark.unit
    def test_effective_onstep_location_prefers_cached_synced_coordinates():
        onstep_props = {
            "LX200 OnStep.GEOGRAPHIC_COORD.LAT": "37.31666666666669983",
            "LX200 OnStep.GEOGRAPHIC_COORD.LONG": "126.8166666666666288",
            "LX200 OnStep.GEOGRAPHIC_COORD.ELEV": "0",
        }
        cache = {
            "latitude": 37.32361,
            "longitude": 126.82194,
            "elevation": 15,
        }

        effective = sys_utils.effective_onstep_location(onstep_props, cache)

        assert effective["latitude"] == pytest.approx(37.32361)
        assert effective["longitude"] == pytest.approx(126.82194)
        assert effective["elevation"] == pytest.approx(15)
        assert effective["source"] == "PiFinder synced location"
        assert effective["driver_readback_matched"] is True
        assert (
            sys_utils.format_effective_onstep_location(onstep_props, cache)
            == "37.32361, 126.82194 / 15m"
        )

    @pytest.mark.unit
    def test_effective_onstep_location_falls_back_to_driver_readback():
        onstep_props = {
            "LX200 OnStep.GEOGRAPHIC_COORD.LAT": "34.25",
            "LX200 OnStep.GEOGRAPHIC_COORD.LONG": "241.75",
            "LX200 OnStep.GEOGRAPHIC_COORD.ELEV": "100",
        }

        effective = sys_utils.effective_onstep_location(onstep_props, {})

        assert effective["latitude"] == pytest.approx(34.25)
        assert effective["longitude"] == pytest.approx(-118.25)
        assert effective["elevation"] == pytest.approx(100)
        assert effective["source"] == "INDI driver readback"

    @pytest.mark.unit
    def test_build_onstep_lx200_location_time_commands_use_onstep_longitude_sign():
        commands = sys_utils.build_onstep_lx200_location_time_commands(
            latitude=37.32361,
            longitude=126.82194,
            elevation=15,
            utc_datetime="2026-06-30T14:15:06+00:00",
            utc_offset_hours=9,
        )

        assert commands[0] == ":St+37*19:25#"
        assert commands[1] == ":Sg-126*49:19#"
        assert commands[2] == ":Sv15#"
        assert commands[3] == ":SG-09:00#"
        assert commands[4] == ":SL23:15:06#"
        assert commands[5] == ":SC06/30/26#"

    @pytest.mark.unit
    def test_build_onstep_lx200_location_time_commands_use_western_offset_sign():
        commands = sys_utils.build_onstep_lx200_location_time_commands(
            latitude=34,
            longitude=-118.25,
            utc_datetime="2026-06-30T13:45:12Z",
            utc_offset_hours=-7,
        )

        assert commands[1] == ":Sg+118*15:00#"
        assert commands[2] == ":SG+07:00#"
        assert commands[3] == ":SL06:45:12#"

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
        result = sys_utils.Network._rewrite_dnsmasq_ap_network(contents, "192.168.50.1")

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
        assert 'ssid="DualBand"' in result

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
            "10.10.10.15 lladdr 06:43:af:65:75:9b REACHABLE\n" "10.10.10.14 FAILED\n"
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
        monkeypatch.setattr(
            sys_utils, "get_default_gpsd_device", lambda: "/dev/ttyAMA3"
        )

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
