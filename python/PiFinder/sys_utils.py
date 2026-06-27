import glob
import configparser
import ipaddress
import json
import re
import subprocess
import time
from typing import Dict, Any

import pam
import requests
import sh
from sh import wpa_cli, unzip, passwd

import socket
from PiFinder import board_config
from PiFinder import utils
import logging

BACKUP_PATH = str(utils.data_dir / "PiFinder_backup.zip")

logger = logging.getLogger("SysUtils")

WIFI_MODE_AP = "AP"
WIFI_MODE_CLIENT = "Client"
WIFI_MODE_APSTA = "AP+STA"
WPA_SUPPLICANT_PATH = "/etc/wpa_supplicant/wpa_supplicant.conf"
BOOT_WPA_SUPPLICANT_PATHS = [
    "/boot/firmware/wpa_supplicant.conf",
    "/boot/wpa_supplicant.conf",
]
NETWORKMANAGER_CONNECTION_GLOB = "/etc/NetworkManager/system-connections/*.nmconnection"
HOSTAPD_CONF_PATH = "/etc/hostapd/hostapd.conf"
HOSTAPD_TMP_PATH = "/tmp/hostapd.conf"
DHCPD_AP_CONF_PATH = "/etc/dhcpcd.conf.ap"
DHCPD_APSTA_CONF_PATH = "/etc/dhcpcd.conf.apsta"
DHCPD_ACTIVE_CONF_PATH = "/etc/dhcpcd.conf"
DNSMASQ_CONF_PATH = "/etc/dnsmasq.conf"
PIFINDER_APSTA_NAT_CONF_PATH = "/etc/pifinder_apsta_nat.conf"
DEFAULT_AP_IP = "10.10.10.1"
AP_SECURITY_OPEN = "OPEN"
AP_SECURITY_WPA2 = "WPA2-PSK"

BLUETOOTHCTL_COMMAND = "bluetoothctl"
BLUETOOTH_DEVICE_RE = re.compile(
    r"(?:\[[^\]]+\]\s*)?Device\s+([0-9A-Fa-f:]{17})\s+(.+)"
)
BLUETOOTH_DEVICE_FIELD_RE = re.compile(
    r"(?:\[[^\]]+\]\s*)?Device\s+([0-9A-Fa-f:]{17})\s+([^:]+):\s+(.+)"
)
BLUETOOTH_MAC_RE = re.compile(r"^[0-9A-Fa-f:]{17}$")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Network:
    """
    Provides wifi network info
    """

    def __init__(self):
        self.wifi_txt = f"{utils.pifinder_dir}/wifi_status.txt"
        with open(self.wifi_txt, "r") as wifi_f:
            self._wifi_mode = wifi_f.read()

        self.populate_wifi_networks()

    def populate_wifi_networks(self) -> None:
        self._wifi_networks = []

        parsed_networks = []
        for path in [WPA_SUPPLICANT_PATH, *BOOT_WPA_SUPPLICANT_PATHS]:
            try:
                with open(path, "r") as wpa_conf:
                    parsed_networks.extend(Network._parse_wpa_supplicant(wpa_conf))
            except FileNotFoundError:
                if path == WPA_SUPPLICANT_PATH:
                    logger.info("wpa_supplicant.conf not found")
                continue
            except IOError as e:
                logger.error(f"Error reading {path}: {e}")

        for path in glob.glob(NETWORKMANAGER_CONNECTION_GLOB):
            try:
                with open(path, "r") as nm_conf:
                    parsed = Network._parse_networkmanager_connection(nm_conf.read())
                    if parsed:
                        parsed_networks.append(parsed)
            except PermissionError:
                logger.info(
                    "Skipping unreadable NetworkManager connection %s; "
                    "setup migration imports these profiles when run with sudo",
                    path,
                )
            except IOError as e:
                logger.error(f"Error reading NetworkManager connection {path}: {e}")

        self._wifi_networks = Network._dedupe_wifi_networks(parsed_networks)

    @staticmethod
    def _dedupe_wifi_networks(networks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        wifi_networks = []
        seen_ssids = set()
        for network in networks:
            ssid = (network.get("ssid") or "").strip()
            if not ssid or ssid in seen_ssids:
                continue
            key_mgmt = network.get("key_mgmt") or "NONE"
            wifi_networks.append(
                {
                    "id": len(wifi_networks),
                    "ssid": ssid,
                    "psk": network.get("psk"),
                    "key_mgmt": key_mgmt,
                }
            )
            seen_ssids.add(ssid)
        return wifi_networks

    @staticmethod
    def _decode_networkmanager_ssid(ssid: str) -> str:
        ssid = ssid.strip()
        if (
            len(ssid) >= 2
            and len(ssid) % 2 == 0
            and re.fullmatch(r"[0-9A-Fa-f]+", ssid)
        ):
            try:
                decoded = bytes.fromhex(ssid).decode("utf-8")
                if decoded.isprintable():
                    return decoded
            except ValueError:
                pass
            except UnicodeDecodeError:
                pass
        return ssid.strip('"')

    @staticmethod
    def _parse_networkmanager_connection(contents: str) -> dict[str, Any] | None:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(contents)
        except configparser.Error as e:
            logger.error(f"Error parsing NetworkManager connection: {e}")
            return None

        wifi_section = None
        for section in ("wifi", "802-11-wireless"):
            if parser.has_section(section):
                wifi_section = section
                break
        if not wifi_section:
            return None

        ssid = Network._decode_networkmanager_ssid(
            parser.get(wifi_section, "ssid", fallback="")
        )
        if not ssid:
            return None

        security_section = None
        for section in ("wifi-security", "802-11-wireless-security"):
            if parser.has_section(section):
                security_section = section
                break

        key_mgmt = "NONE"
        psk = None
        if security_section:
            nm_key_mgmt = parser.get(security_section, "key-mgmt", fallback="")
            psk = parser.get(security_section, "psk", fallback=None)
            if nm_key_mgmt.lower() in ("wpa-psk", "sae") and psk:
                key_mgmt = "WPA-PSK"
            elif nm_key_mgmt:
                key_mgmt = nm_key_mgmt.upper()

        return {"id": 0, "ssid": ssid, "psk": psk, "key_mgmt": key_mgmt}

    @staticmethod
    def _parse_wpa_supplicant(contents: list[str]) -> list:
        """
        Parses wpa_supplicant.conf to get current config
        """
        wifi_networks = []
        network_dict: Dict[str, Any] = {}
        network_id = 0
        in_network_block = False
        for line in contents:
            line = line.strip()
            if line.startswith("network={"):
                in_network_block = True
                network_dict = {
                    "id": network_id,
                    "ssid": None,
                    "psk": None,
                    "key_mgmt": None,
                }

            elif line == "}" and in_network_block:
                in_network_block = False
                wifi_networks.append(network_dict)
                network_id += 1

            elif in_network_block:
                match = re.match(r"(\w+)=(.+)", line)
                if match:
                    key, value = match.groups()
                    if key in network_dict:
                        network_dict[key] = value.strip('"')

        return wifi_networks

    def get_wifi_networks(self):
        return self._wifi_networks

    @staticmethod
    def _quote_wpa_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def delete_wifi_network(self, network_id):
        """
        Immediately deletes a wifi network
        """
        self._wifi_networks.pop(network_id)

        with open(WPA_SUPPLICANT_PATH, "r") as wpa_conf:
            wpa_contents = list(wpa_conf)

        with open(WPA_SUPPLICANT_PATH, "w") as wpa_conf:
            in_networks = False
            for line in wpa_contents:
                if not in_networks:
                    if line.startswith("network={"):
                        in_networks = True
                    else:
                        wpa_conf.write(line)

            for network in self._wifi_networks:
                ssid = Network._quote_wpa_value(network["ssid"])
                key_mgmt = network["key_mgmt"]
                psk = Network._quote_wpa_value(network["psk"] or "")

                wpa_conf.write("\nnetwork={\n")
                wpa_conf.write(f'\tssid="{ssid}"\n')
                if key_mgmt == "WPA-PSK" and psk:
                    wpa_conf.write(f'\tpsk="{psk}"\n')
                wpa_conf.write(f"\tkey_mgmt={key_mgmt}\n")

                wpa_conf.write("}\n")

        self.populate_wifi_networks()

    def add_wifi_network(self, ssid, key_mgmt, psk=None):
        """
        Add a wifi network
        """
        ssid = (ssid or "").strip()
        psk = (psk or "").strip()
        if not ssid:
            raise ValueError("SSID is required")
        if key_mgmt == "WPA-PSK" and len(psk) < 8:
            raise ValueError("Wi-Fi password must be at least 8 characters")

        with open(WPA_SUPPLICANT_PATH, "a") as wpa_conf:
            wpa_conf.write("\nnetwork={\n")
            wpa_conf.write(f'\tssid="{Network._quote_wpa_value(ssid)}"\n')
            if key_mgmt == "WPA-PSK":
                wpa_conf.write(f'\tpsk="{Network._quote_wpa_value(psk)}"\n')
            wpa_conf.write(f"\tkey_mgmt={key_mgmt}\n")

            wpa_conf.write("}\n")

        self.populate_wifi_networks()
        if self._wifi_mode in (WIFI_MODE_CLIENT, WIFI_MODE_APSTA):
            # Restart the supplicant
            wpa_cli("reconfigure")

    @staticmethod
    def _parse_iw_scan(output: str) -> list[str]:
        networks = []
        seen = set()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped.startswith("SSID:"):
                continue
            ssid = stripped[5:].strip()
            if not ssid or ssid in seen:
                continue
            networks.append(ssid)
            seen.add(ssid)
        return networks

    def scan_wifi_networks(self) -> list[str]:
        """
        Scan nearby Wi-Fi networks and return a deduplicated SSID list.
        """
        scan_commands = [
            ["iw", "dev", "wlan0", "scan"],
            ["sudo", "-n", "iw", "dev", "wlan0", "scan"],
            ["iwlist", "wlan0", "scan"],
            ["sudo", "-n", "iwlist", "wlan0", "scan"],
        ]
        for command in scan_commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
            output = result.stdout + result.stderr
            if "iwlist" in command:
                networks = Network._parse_iwlist_scan(output)
            else:
                networks = Network._parse_iw_scan(output)
            if networks:
                return networks
        return []

    @staticmethod
    def _parse_iwlist_scan(output: str) -> list[str]:
        networks = []
        seen = set()
        for match in re.finditer(r'ESSID:"([^"]*)"', output):
            ssid = match.group(1).strip()
            if not ssid or ssid in seen:
                continue
            networks.append(ssid)
            seen.add(ssid)
        return networks

    def get_ap_name(self):
        with open(HOSTAPD_CONF_PATH, "r") as conf:
            for line in conf:
                if line.startswith("ssid="):
                    return line[5:-1]
        return "UNKN"

    @staticmethod
    def _rewrite_key_value_lines(
        lines: list[str], updates: dict[str, str], remove_keys: set[str] | None = None
    ) -> list[str]:
        remove_keys = remove_keys or set()
        remaining_updates = dict(updates)
        rewritten = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in remove_keys:
                continue
            if key in remaining_updates:
                rewritten.append(f"{key}={remaining_updates.pop(key)}\n")
                continue
            rewritten.append(line)
        for key, value in remaining_updates.items():
            rewritten.append(f"{key}={value}\n")
        return rewritten

    def _write_hostapd_config(
        self, updates: dict[str, str], remove_keys: set[str] | None = None
    ) -> None:
        with open(HOSTAPD_CONF_PATH, "r") as conf:
            lines = list(conf)
        lines = Network._rewrite_key_value_lines(lines, updates, remove_keys)
        with open(HOSTAPD_TMP_PATH, "w") as new_conf:
            new_conf.writelines(lines)
        sh.sudo("cp", HOSTAPD_TMP_PATH, HOSTAPD_CONF_PATH)

    def set_ap_name(self, ap_name):
        if ap_name == self.get_ap_name():
            return
        ap_name = (ap_name or "").strip()
        if not ap_name or "\n" in ap_name:
            raise ValueError("AP network name is invalid")
        self._write_hostapd_config({"ssid": ap_name})

    def _get_hostapd_value(self, key: str) -> str:
        with open(HOSTAPD_CONF_PATH, "r") as conf:
            for line in conf:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
        return ""

    def get_ap_security(self):
        if self._get_hostapd_value("wpa") or self._get_hostapd_value(
            "wpa_passphrase"
        ):
            return AP_SECURITY_WPA2
        return AP_SECURITY_OPEN

    def get_ap_password(self):
        return self._get_hostapd_value("wpa_passphrase")

    def set_ap_security(self, security, password):
        security = (security or AP_SECURITY_OPEN).strip().upper()
        password = (password or "").strip()
        if security in ("OPEN", "NONE"):
            self._write_hostapd_config(
                {},
                {
                    "wpa",
                    "wpa_passphrase",
                    "wpa_key_mgmt",
                    "wpa_pairwise",
                    "rsn_pairwise",
                },
            )
            return

        if security not in (AP_SECURITY_WPA2, "WPA2"):
            raise ValueError("Unsupported AP security mode")

        if not password:
            password = self.get_ap_password()
        if len(password) < 8 or len(password) > 63:
            raise ValueError("AP password must be 8 to 63 characters")
        if "\n" in password:
            raise ValueError("AP password is invalid")

        self._write_hostapd_config(
            {
                "wpa": "2",
                "wpa_passphrase": password,
                "wpa_key_mgmt": "WPA-PSK",
                "rsn_pairwise": "CCMP",
            },
            {"wpa_pairwise"},
        )

    @staticmethod
    def _validate_ap_ip(ap_ip: str) -> ipaddress.IPv4Address:
        try:
            ip = ipaddress.IPv4Address((ap_ip or "").strip())
        except ipaddress.AddressValueError as e:
            raise ValueError("AP IP address is invalid") from e

        if (
            not ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("AP IP address must be a private IPv4 address")
        return ip

    @staticmethod
    def _ap_ip_cidr(ap_ip: str) -> str:
        ip = Network._validate_ap_ip(ap_ip)
        return f"{ip}/24"

    @staticmethod
    def _ap_dhcp_range(ap_ip: str) -> tuple[str, str]:
        interface = ipaddress.IPv4Interface(Network._ap_ip_cidr(ap_ip))
        ap_address = interface.ip
        candidates = [host for host in interface.network.hosts() if host != ap_address]
        if len(candidates) < 2:
            raise ValueError("AP network is too small")
        end_index = min(19, len(candidates))
        return str(candidates[0]), str(candidates[end_index - 1])

    @staticmethod
    def _rewrite_dhcpcd_static_ip(contents: str, interface: str, ap_ip: str) -> str:
        lines = contents.splitlines(keepends=True)
        target_line = f"    static ip_address={Network._ap_ip_cidr(ap_ip)}\n"
        in_interface = False
        saw_interface = False
        replaced = False
        output = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("interface "):
                if in_interface and not replaced:
                    output.append(target_line)
                    replaced = True
                in_interface = stripped == f"interface {interface}"
                saw_interface = saw_interface or in_interface
                output.append(line)
                continue

            if in_interface and stripped.startswith("static ip_address="):
                if not replaced:
                    output.append(target_line)
                    replaced = True
                continue

            output.append(line)

        if in_interface and not replaced:
            output.append(target_line)
        if not saw_interface:
            if output and not output[-1].endswith("\n"):
                output[-1] += "\n"
            output.extend([f"\ninterface {interface}\n", target_line])
        return "".join(output)

    @staticmethod
    def _write_root_file(path: str, contents: str) -> None:
        tmp_path = f"/tmp/{path.strip('/').replace('/', '_')}"
        with open(tmp_path, "w") as tmp_file:
            tmp_file.write(contents)
        sh.sudo("cp", tmp_path, path)

    def _update_dhcpcd_ap_file(self, path: str, interface: str, ap_ip: str) -> None:
        try:
            with open(path, "r") as conf:
                contents = conf.read()
        except FileNotFoundError:
            contents = ""
        Network._write_root_file(
            path, Network._rewrite_dhcpcd_static_ip(contents, interface, ap_ip)
        )

    @staticmethod
    def _rewrite_dnsmasq_ap_network(contents: str, ap_ip: str) -> str:
        start, end = Network._ap_dhcp_range(ap_ip)
        return "".join(
            Network._rewrite_key_value_lines(
                contents.splitlines(keepends=True),
                {
                    "dhcp-range": f"{start},{end},255.255.255.0,24h",
                    "address": f"/gw.wlan/{ap_ip}",
                },
            )
        )

    def _update_dnsmasq_ap_network(self, ap_ip: str) -> None:
        try:
            with open(DNSMASQ_CONF_PATH, "r") as conf:
                contents = conf.read()
        except FileNotFoundError:
            contents = ""
        Network._write_root_file(
            DNSMASQ_CONF_PATH, Network._rewrite_dnsmasq_ap_network(contents, ap_ip)
        )

    @staticmethod
    def _parse_dhcpcd_static_ip(contents: str, interface: str) -> str:
        in_interface = False
        for line in contents.splitlines():
            stripped = line.strip()
            if stripped.startswith("interface "):
                in_interface = stripped == f"interface {interface}"
                continue
            if in_interface and stripped.startswith("static ip_address="):
                cidr = stripped.split("=", 1)[1]
                return str(ipaddress.IPv4Interface(cidr).ip)
        return DEFAULT_AP_IP

    def get_ap_ip(self):
        try:
            with open(DHCPD_AP_CONF_PATH, "r") as conf:
                return Network._parse_dhcpcd_static_ip(conf.read(), "wlan0")
        except Exception:
            return DEFAULT_AP_IP

    def set_ap_ip(self, ap_ip):
        ap_ip = str(Network._validate_ap_ip(ap_ip))
        if ap_ip == self.get_ap_ip():
            return

        self._update_dhcpcd_ap_file(DHCPD_AP_CONF_PATH, "wlan0", ap_ip)
        self._update_dhcpcd_ap_file(DHCPD_APSTA_CONF_PATH, "uap0", ap_ip)
        if self._wifi_mode == WIFI_MODE_AP:
            self._update_dhcpcd_ap_file(DHCPD_ACTIVE_CONF_PATH, "wlan0", ap_ip)
        elif self._wifi_mode == WIFI_MODE_APSTA:
            self._update_dhcpcd_ap_file(DHCPD_ACTIVE_CONF_PATH, "uap0", ap_ip)
        self._update_dnsmasq_ap_network(ap_ip)

    @staticmethod
    def _parse_apsta_nat_config(contents: str) -> bool:
        for line in contents.splitlines():
            if line.strip() == "PIFINDER_APSTA_SHARE_INTERNET=1":
                return True
        return False

    def get_apsta_internet_sharing(self):
        try:
            with open(PIFINDER_APSTA_NAT_CONF_PATH, "r") as conf:
                return Network._parse_apsta_nat_config(conf.read())
        except FileNotFoundError:
            return False
        except IOError as e:
            logger.error(f"Error reading AP+STA internet sharing config: {e}")
            return False

    def set_apsta_internet_sharing(self, enabled):
        enabled_value = "1" if enabled else "0"
        contents = (
            "# PiFinder AP+STA internet sharing setting\n"
            f"PIFINDER_APSTA_SHARE_INTERNET={enabled_value}\n"
        )
        Network._write_root_file(PIFINDER_APSTA_NAT_CONF_PATH, contents)

    def get_host_name(self):
        return socket.gethostname()

    def get_connected_ssid(self) -> str:
        """
        Returns the SSID of the connected wifi network or
        None if not connected or in AP mode
        """
        if self.wifi_mode() == WIFI_MODE_AP:
            return ""
        # get output from iwgetid
        try:
            iwgetid = sh.Command("iwgetid")
            _t = iwgetid("wlan0", _ok_code=(0, 255)).strip()
            if not _t:
                _t = iwgetid(_ok_code=(0, 255)).strip()
            return _t.split(":")[-1].strip('"')
        except sh.CommandNotFound:
            return "ssid_not_found"

    def set_host_name(self, hostname) -> None:
        if hostname == self.get_host_name():
            return
        _result = sh.sudo("hostnamectl", "set-hostname", hostname)
        self._update_etc_hosts(hostname)

    @staticmethod
    def _rewrite_hosts(contents: str, new_hostname: str) -> str:
        """
        Rewrite the Debian-convention ``127.0.1.1`` line in /etc/hosts to point
        at ``new_hostname``. Preserves indentation, the IP, and any trailing
        aliases/comments. If no ``127.0.1.1`` line exists, appends one so that
        ``sudo`` can still resolve the host.
        """
        lines = contents.splitlines(keepends=True)
        pattern = re.compile(r"^(\s*127\.0\.1\.1\s+)\S+(.*)$")
        replaced = False
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                eol = "\n" if line.endswith("\n") else ""
                lines[i] = f"{match.group(1)}{new_hostname}{match.group(2)}{eol}"
                replaced = True
                break
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"127.0.1.1\t{new_hostname}\n")
        return "".join(lines)

    def _update_etc_hosts(self, new_hostname: str) -> None:
        try:
            with open("/etc/hosts", "r") as hosts_f:
                contents = hosts_f.read()
        except IOError as e:
            logger.error(f"Error reading /etc/hosts: {e}")
            return
        new_contents = Network._rewrite_hosts(contents, new_hostname)
        with open("/tmp/hosts", "w") as new_hosts:
            new_hosts.write(new_contents)
        sh.sudo("cp", "/tmp/hosts", "/etc/hosts")

    def wifi_mode(self):
        return self._wifi_mode

    def set_wifi_mode(self, mode):
        if mode == self._wifi_mode:
            return
        if mode == WIFI_MODE_AP:
            go_wifi_ap()

        if mode == WIFI_MODE_CLIENT:
            go_wifi_cli()

        if mode == WIFI_MODE_APSTA:
            go_wifi_apsta()

    def _route_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.255.255.255", 1))
            return s.getsockname()[0]
        except Exception:
            return "NONE"
        finally:
            s.close()

    def local_ip(self):
        if self._wifi_mode == WIFI_MODE_AP:
            return "10.10.10.1"
        if self._wifi_mode == WIFI_MODE_APSTA:
            sta_ip = self._route_ip()
            if sta_ip == "NONE":
                return "10.10.10.1"
            return f"{sta_ip} / 10.10.10.1"

        return self._route_ip()


def go_wifi_ap():
    logger.info("SYS: Switching to AP")
    sh.sudo(str(utils.pifinder_dir / "switch-ap.sh"))
    return True


def go_wifi_cli():
    logger.info("SYS: Switching to Client")
    sh.sudo(str(utils.pifinder_dir / "switch-cli.sh"))
    return True


def go_wifi_apsta():
    logger.info("SYS: Switching to AP+STA")
    sh.sudo(str(utils.pifinder_dir / "switch-apsta.sh"))
    return True


def _clean_bluetoothctl_output(output: str) -> str:
    output = ANSI_ESCAPE_RE.sub("", output)
    return output.replace("\r", "\n")


def _bluetoothctl(commands: list[str], timeout: int = 20) -> str:
    script = "\n".join(commands + ["quit"]) + "\n"
    result = subprocess.run(
        [BLUETOOTHCTL_COMMAND],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return _clean_bluetoothctl_output(result.stdout + result.stderr)


def _parse_bluetooth_bool(value: str) -> bool:
    return value.strip().lower() == "yes"


def _new_bluetooth_device(address: str, name: str = "") -> dict[str, Any]:
    return {
        "address": address,
        "name": name,
        "paired": False,
        "trusted": False,
        "connected": False,
        "blocked": False,
        "icon": "",
    }


def _has_bluetooth_name(device: dict[str, Any]) -> bool:
    name = str(device.get("name", "")).strip()
    return bool(name) and not BLUETOOTH_MAC_RE.match(name)


def _merge_bluetooth_device_name(device: dict[str, Any], name: str) -> None:
    name = name.strip()
    if not name or BLUETOOTH_MAC_RE.match(name):
        return
    if not _has_bluetooth_name(device):
        device["name"] = name


def _parse_bluetooth_devices(output: str) -> dict[str, dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    for line in _clean_bluetoothctl_output(output).splitlines():
        field_match = BLUETOOTH_DEVICE_FIELD_RE.search(line)
        if field_match:
            address = field_match.group(1).upper()
            field = field_match.group(2).strip().lower()
            value = field_match.group(3).strip()
            device = devices.setdefault(address, _new_bluetooth_device(address))
            if field in ["name", "alias"]:
                _merge_bluetooth_device_name(device, value)
                continue
            elif field in ["paired", "trusted", "connected", "blocked"]:
                device[field] = _parse_bluetooth_bool(value)
                continue
            elif field == "icon":
                device["icon"] = value
                continue
            elif field in ["rssi", "txpower", "uuids", "servicesresolved"]:
                continue

        match = BLUETOOTH_DEVICE_RE.search(line)
        if not match:
            continue
        address = match.group(1).upper()
        name = match.group(2).strip()
        device = devices.setdefault(address, _new_bluetooth_device(address))
        _merge_bluetooth_device_name(device, name)
    return devices


def _parse_bluetooth_info(output: str) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for raw_line in _clean_bluetoothctl_output(output).splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key in ["Name", "Alias", "Icon"]:
            info[key.lower()] = value
        elif key in ["Paired", "Trusted", "Connected", "Blocked"]:
            info[key.lower()] = _parse_bluetooth_bool(value)
    return info


def is_bluetooth_keyboard(device: dict[str, Any]) -> bool:
    """
    Best-effort keyboard detection for reconnect filtering.
    """
    name = str(device.get("name", "")).lower()
    icon = str(device.get("icon", "")).lower()
    return "keyboard" in name or "keys" in name or icon == "input-keyboard"


def list_bluetooth_devices(scan_output: str = "") -> list[dict[str, Any]]:
    """
    Return cached Bluetooth devices with paired/trusted/connected status.
    """
    logger.info("SYS: Listing Bluetooth devices")
    devices = _parse_bluetooth_devices(scan_output)
    output = _bluetoothctl(["power on", "devices", "devices Paired"], timeout=12)
    for address, scanned_device in _parse_bluetooth_devices(output).items():
        device = devices.setdefault(address, _new_bluetooth_device(address))
        _merge_bluetooth_device_name(device, str(scanned_device.get("name", "")))

    for address, device in devices.items():
        info = _parse_bluetooth_info(
            _bluetoothctl([f"info {address}"], timeout=8)
        )
        for key, value in info.items():
            if key in ["name", "alias"]:
                _merge_bluetooth_device_name(device, str(value))
            else:
                device[key] = value
        device["address"] = address
        device["name"] = device.get("name") or device.get("alias") or address

    return sorted(
        devices.values(),
        key=lambda item: (
            not bool(item.get("connected")),
            not bool(item.get("paired")),
            str(item.get("name", "")).lower(),
        ),
    )


def scan_bluetooth_devices(scan_seconds: int = 12) -> list[dict[str, Any]]:
    """
    Scan for nearby Bluetooth devices and return the refreshed cached device list.
    """
    logger.info("SYS: Scanning for Bluetooth devices for %s seconds", scan_seconds)
    process = subprocess.Popen(
        [BLUETOOTHCTL_COMMAND],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdin is not None
    scan_output = ""
    try:
        for command in [
            "power on",
            "agent KeyboardDisplay",
            "default-agent",
            "pairable on",
            "scan on",
        ]:
            process.stdin.write(command + "\n")
            process.stdin.flush()

        time.sleep(scan_seconds)

        for command in ["scan off", "devices", "devices Paired", "quit"]:
            process.stdin.write(command + "\n")
            process.stdin.flush()
        scan_output, _ = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        scan_output, _ = process.communicate()
    finally:
        if process.poll() is None:
            process.terminate()

    return list_bluetooth_devices(scan_output)


def connect_bluetooth_device(address: str, timeout: int = 25) -> str:
    logger.info("SYS: Connecting Bluetooth device %s", address)
    return _bluetoothctl(
        [
            "power on",
            "agent KeyboardDisplay",
            "default-agent",
            f"trust {address}",
            f"connect {address}",
        ],
        timeout=timeout,
    )


def disconnect_bluetooth_device(address: str) -> str:
    logger.info("SYS: Disconnecting Bluetooth device %s", address)
    return _bluetoothctl([f"disconnect {address}"], timeout=15)


def remove_bluetooth_device(address: str) -> str:
    logger.info("SYS: Removing Bluetooth device %s", address)
    return _bluetoothctl([f"remove {address}"], timeout=15)


def reconnect_bluetooth_keyboards(connect_timeout: int = 25) -> int:
    """
    Connect paired keyboard-like devices. If none are identifiable as keyboards,
    try all paired devices so generic HID names still work.
    """
    devices = [
        d for d in list_bluetooth_devices() if d.get("paired") or d.get("trusted")
    ]
    targets = [d for d in devices if is_bluetooth_keyboard(d)] or devices
    count = 0
    for device in targets:
        if device.get("connected"):
            continue
        connect_bluetooth_device(str(device["address"]), timeout=connect_timeout)
        count += 1
    return count


def auto_reconnect_bluetooth_keyboards(
    attempts: int = 12,
    delay_seconds: int = 5,
    connect_timeout: int = 10,
) -> int:
    """
    Retry Bluetooth keyboard reconnection in the background during startup.

    Bluetooth controllers and HID devices can appear a few seconds after the
    PiFinder service starts, especially after a reboot. This helper is designed
    for a daemon thread: it logs failures and keeps retrying without raising.
    """
    total_attempted = 0
    for attempt in range(1, attempts + 1):
        try:
            logger.info(
                "SYS: Bluetooth keyboard reconnect attempt %s/%s",
                attempt,
                attempts,
            )
            total_attempted += reconnect_bluetooth_keyboards(connect_timeout)
            devices = [
                d
                for d in list_bluetooth_devices()
                if d.get("paired") or d.get("trusted")
            ]
            targets = [d for d in devices if is_bluetooth_keyboard(d)] or devices
            if targets and any(d.get("connected") for d in targets):
                logger.info("SYS: Bluetooth keyboard reconnect complete")
                return total_attempted
        except Exception as e:
            logger.warning("SYS: Bluetooth keyboard reconnect failed: %s", e)

        if attempt < attempts:
            time.sleep(delay_seconds)

    logger.info(
        "SYS: Bluetooth keyboard reconnect finished after %s attempts",
        attempts,
    )
    return total_attempted


def remove_backup():
    """
    Removes backup file
    """
    sh.sudo("rm", BACKUP_PATH, _ok_code=(0, 1))


def backup_userdata():
    """
    Back up userdata to a single zip file for later
    restore.  Returns the path to the zip file.

    Backs up:
        config.json
        observations.db
        obslist/*
    """

    remove_backup()

    _zip = sh.Command("zip")
    _zip(
        BACKUP_PATH,
        str(utils.data_dir / "config.json"),
        str(utils.data_dir / "observations.db"),
        glob.glob(str(utils.data_dir / "obslists" / "*")),
    )

    return BACKUP_PATH


def restore_userdata(zip_path):
    """
    Compliment to backup_userdata
    restores userdata
    OVERWRITES existing data!
    """
    unzip("-d", "/", "-o", zip_path)


def restart_pifinder() -> None:
    """
    Uses systemctl to restart the PiFinder
    service
    """
    logger.info("SYS: Restarting PiFinder")
    sh.sudo("systemctl", "restart", "pifinder")


def restart_system() -> None:
    """
    Restarts the system
    """
    logger.info("SYS: Initiating System Restart")
    sh.sudo("shutdown", "-r", "now")


def shutdown() -> None:
    """
    shuts down the system
    """
    logger.info("SYS: Initiating Shutdown")
    sh.sudo("shutdown", "now")


def update_software():
    """
    Uses systemctl to git pull and then restart
    service
    """
    logger.info("SYS: Running update")
    sh.bash(str(utils.pifinder_dir / "pifinder_update.sh"))
    return True


def verify_password(username, password):
    """
    Checks the provided password against the provided user
    password
    """
    p = pam.pam()

    return p.authenticate(username, password)


def change_password(username, current_password, new_password):
    """
    Changes the PiFinder User password
    """
    result = passwd(
        username,
        _in=f"{current_password}\n{new_password}\n{new_password}\n",
        _ok_code=(0, 10),
    )

    if result.exit_code == 0:
        return True
    else:
        return False


def switch_cam_imx477() -> None:
    logger.info("SYS: Switching cam to imx477")
    sh.sudo("python", "-m", "PiFinder.switch_camera", "imx477")


def switch_cam_imx296() -> None:
    logger.info("SYS: Switching cam to imx296")
    sh.sudo("python", "-m", "PiFinder.switch_camera", "imx296")


def switch_cam_imx462() -> None:
    logger.info("SYS: Switching cam to imx462")
    sh.sudo("python", "-m", "PiFinder.switch_camera", "imx462")


def get_default_gpsd_device() -> str:
    return board_config.get_default_gpsd_device()


DEFAULT_GPSD_DEVICE = get_default_gpsd_device()


def resolve_gpsd_device(device: str | None) -> str:
    if not device or device == "auto":
        return get_default_gpsd_device()
    return device


def _gpsd_options_line(baud_rate: int) -> str:
    if baud_rate == 115200:
        # NOTE: the space before -s in the next line is really needed
        return 'GPSD_OPTIONS=" -s 115200"'
    return 'GPSD_OPTIONS=""'


def _gpsd_devices_line(device: str) -> str:
    return f'DEVICES="{device}"'


def check_and_sync_gpsd_config(
    baud_rate: int, device: str = DEFAULT_GPSD_DEVICE
) -> bool:
    """
    Checks if GPSD configuration matches the desired serial device and baud rate,
    and updates it only if necessary.

    Args:
        baud_rate: The desired baud rate (9600 or 115200)
        device: The serial device path to configure for gpsd

    Returns:
        True if configuration was updated, False if already correct
    """
    device = resolve_gpsd_device(device)
    logger.info(f"SYS: Checking GPSD config for device {device}, baud rate {baud_rate}")

    try:
        # Read current config
        with open("/etc/default/gpsd", "r") as f:
            content = f.read()

        expected_devices = _gpsd_devices_line(device)
        expected_options = _gpsd_options_line(baud_rate)

        # Check if update is needed
        current_match = re.search(r"^GPSD_OPTIONS=.*$", content, re.MULTILINE)
        current_options = current_match.group(0) if current_match else ""
        current_match = re.search(r"^DEVICES=.*$", content, re.MULTILINE)
        current_devices = current_match.group(0) if current_match else ""
        if current_options == expected_options and current_devices == expected_devices:
            logger.info("SYS: GPSD config already correct, no update needed")
            return False

        # Update is needed
        logger.info(
            "SYS: GPSD config mismatch, updating to %s, %s",
            expected_devices,
            expected_options,
        )
        update_gpsd_config(baud_rate, device)
        return True

    except Exception as e:
        logger.error(f"SYS: Error checking/syncing GPSD config: {e}")
        return False


def update_gpsd_config(baud_rate: int, device: str = DEFAULT_GPSD_DEVICE) -> None:
    """
    Updates the GPSD configuration file with the specified device and baud rate
    and restarts the GPSD service.

    Args:
        baud_rate: The baud rate to configure (9600 or 115200)
        device: The serial device path to configure for gpsd
    """
    device = resolve_gpsd_device(device)
    logger.info(f"SYS: Updating GPSD config with device {device}, baud rate {baud_rate}")

    try:
        # Read the current config
        with open("/etc/default/gpsd", "r") as f:
            lines = f.readlines()

        expected_devices = _gpsd_devices_line(device)
        expected_options = _gpsd_options_line(baud_rate)

        # Update DEVICES and GPSD_OPTIONS lines
        updated_lines = []
        saw_devices = False
        saw_options = False
        for line in lines:
            if line.startswith("DEVICES="):
                updated_lines.append(f"{expected_devices}\n")
                saw_devices = True
            elif line.startswith("GPSD_OPTIONS="):
                updated_lines.append(f"{expected_options}\n")
                saw_options = True
            else:
                updated_lines.append(line)
        if not saw_devices:
            updated_lines.append(f"{expected_devices}\n")
        if not saw_options:
            updated_lines.append(f"{expected_options}\n")

        # Write the updated config to a temporary file
        with open("/tmp/gpsd.conf", "w") as f:
            f.writelines(updated_lines)

        # Copy the temp file to the actual location with sudo
        sh.sudo("cp", "/tmp/gpsd.conf", "/etc/default/gpsd")

        # Restart GPSD service
        sh.sudo("systemctl", "restart", "gpsd")

        logger.info("SYS: GPSD configuration updated and service restarted")

    except Exception as e:
        logger.error(f"SYS: Error updating GPSD config: {e}")
        raise


# ---------------------------------------------------------------------------
# NixOS migration
# ---------------------------------------------------------------------------

MIGRATION_PROGRESS_FILE = "/tmp/nixos_migration_progress"
MIGRATION_SCRIPT = str(utils.pifinder_dir / "python/scripts/nixos_migration.sh")


def _fetch_migration_sha256(version_info: dict) -> str:
    """Fetch SHA256 from sidecar URL, falling back to hardcoded value."""
    sha256_url = version_info.get("migration_sha256_url", "")
    if sha256_url:
        try:
            resp = requests.get(sha256_url, timeout=15)
            if resp.status_code == 200:
                sha256 = resp.text.strip().split()[0]
                logger.info(f"SYS: Fetched migration SHA256: {sha256[:16]}...")
                return sha256
            logger.warning(f"SYS: SHA256 fetch returned {resp.status_code}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"SYS: Failed to fetch SHA256: {e}")

    sha256 = version_info.get("migration_sha256", "")
    if sha256:
        logger.info("SYS: Using hardcoded migration SHA256")
    return sha256


def start_nixos_migration(version_info: dict) -> None:
    """
    Start the NixOS migration process in the background.

    Raises ValueError if migration_url or a migration SHA256 cannot be
    obtained — an in-place OS replacement must not run without checksum
    verification.
    """
    url = version_info.get("migration_url", "")
    if not url:
        raise ValueError("Missing migration_url")
    sha256 = _fetch_migration_sha256(version_info)
    if not sha256:
        raise ValueError(
            "No migration SHA256 available (neither migration_sha256_url nor "
            "migration_sha256 produced a value); refusing to migrate without "
            "checksum verification"
        )
    display_class = str(version_info.get("display_class", ""))
    display_resolution_value = version_info.get("display_resolution", "")
    if isinstance(display_resolution_value, (list, tuple)):
        display_resolution = "x".join(str(part) for part in display_resolution_value)
    else:
        display_resolution = str(display_resolution_value)

    logger.info(f"SYS: Starting NixOS migration to {version_info.get('version', '?')}")

    with open(MIGRATION_PROGRESS_FILE, "w") as f:
        json.dump({"percent": 0, "status": "Starting..."}, f)

    def _log_output(line):
        logger.info(f"SYS: migration: {line.strip()}")

    def _log_error(line):
        logger.error(f"SYS: migration: {line.strip()}")

    def _on_done(cmd, success, exit_code):
        if not success:
            logger.error(f"SYS: Migration script failed with exit code {exit_code}")

    try:
        sh.bash(
            MIGRATION_SCRIPT,
            url,
            sha256,
            MIGRATION_PROGRESS_FILE,
            display_class,
            display_resolution,
            _bg=True,
            _bg_exc=False,
            _out=_log_output,
            _err=_log_error,
            _done=_on_done,
        )
    except Exception as e:
        logger.error(f"SYS: Migration failed to start: {e}")
        raise


def get_migration_progress() -> Dict[str, Any]:
    """
    Read current migration progress from the progress file.
    """
    try:
        with open(MIGRATION_PROGRESS_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
