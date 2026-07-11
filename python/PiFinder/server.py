import io
import json
import logging
import time
import uuid
import os
import argparse
import sys
import multiprocessing
import threading
from datetime import datetime, timezone

import pydeepskylog as pds
from PIL import Image
from PiFinder import timez
from PiFinder import utils, calc_utils, config, location_catalog
from PiFinder.db.observations_db import (
    ObservationsDatabase,
)
from PiFinder.equipment import Telescope, Eyepiece
from PiFinder.indi_align import BRIGHT_ALIGN_STARS, clamp_align_points, get_align_star
from PiFinder.keyboard_interface import KeyboardInterface
from PiFinder.multiproclogging import MultiprocLogging
from PiFinder.livecam_config import settings_from_config

from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    redirect,
    session,
    make_response,
)
from urllib.parse import quote
from flask_babel import Babel, gettext  # type: ignore[import-untyped]
from werkzeug.routing import IntegerConverter
from waitress import serve as waitress_serve

from PiFinder import i18n  # noqa: F401

_ = gettext


# Custom converter to handle negative integers in Flask routes
class SignedIntConverter(IntegerConverter):
    regex = r"-?\d+"


sys_utils = utils.get_sys_utils()

logger = logging.getLogger("Server")
logs_logger = logging.getLogger("Server.Logs")

# Generate a secret to validate the auth cookie
SESSION_SECRET = str(uuid.uuid4())
WEB_MOTION_LEASE_SECONDS = 1.2
WEB_MOTION_KEEPALIVE_INTERVAL = 0.4
WEB_BACKLASH_MIN_VALUE = 0
WEB_BACKLASH_MAX_VALUE = 3600
WEB_BACKLASH_DEFAULT_REPEATS = 10
WEB_BACKLASH_MIN_REPEATS = 1
WEB_BACKLASH_MAX_REPEATS = 50
WEB_BACKLASH_STOP_REQUEST_FILE = utils.data_dir / "mount_control_stop_request.json"
DEFAULT_WEB_LANGUAGE = "en"


def auth_required(func):
    def auth_wrapper(*args, **kwargs):
        # check for and validate session
        if "authenticated" in session and session["authenticated"]:
            return func(*args, **kwargs)

        # Pass the original URL via ?next= so Safari preserves it across redirects
        return redirect(f"/login?next={quote(request.url, safe='')}")

    auth_wrapper.__name__ = func.__name__
    return auth_wrapper


class MockSharedState:
    """Mock shared state for standalone testing"""

    def __init__(self):
        self._location = type(
            "Location", (), {"lock": False, "lat": None, "lon": None, "altitude": None}
        )()
        self._screen_img = None
        self._solve_state = False
        self._solution = None
        self._raw_live_frame = None
        self._livecam_settings = {}

    def location(self):
        return self._location

    def screen(self):
        return self._screen_img

    def solve_state(self):
        return self._solve_state

    def solution(self):
        return self._solution

    def raw_live_frame(self):
        return self._raw_live_frame

    def set_raw_live_frame(self, value):
        self._raw_live_frame = value

    def livecam_settings(self):
        return dict(self._livecam_settings)

    def set_livecam_settings(self, value):
        self._livecam_settings = dict(value or {})


def server_locale():
    return DEFAULT_WEB_LANGUAGE


class Server:
    def __init__(
        self,
        keyboard_queue=None,
        ui_queue=None,
        gps_queue=None,
        mountcontrol_queue=None,
        shared_state=None,
        is_debug=False,
    ):
        self.version_txt = f"{utils.pifinder_dir}/version.txt"
        self.keyboard_queue = keyboard_queue or multiprocessing.Queue()
        self.ui_queue = ui_queue or multiprocessing.Queue()
        self.gps_queue = gps_queue or multiprocessing.Queue()
        self.mountcontrol_queue = mountcontrol_queue
        self.shared_state = shared_state or MockSharedState()
        if hasattr(self.shared_state, "set_livecam_settings"):
            self.shared_state.set_livecam_settings(
                settings_from_config(config.Config())
            )
        self.ki = KeyboardInterface()
        # gps info
        self.lat = None
        self.lon = None
        self.altitude = None
        self.gps_locked = False

        self.button_dict = {
            "PLUS": self.ki.PLUS,
            "MINUS": self.ki.MINUS,
            "SQUARE": self.ki.SQUARE,
            "LEFT": self.ki.LEFT,
            "UP": self.ki.UP,
            "DOWN": self.ki.DOWN,
            "RIGHT": self.ki.RIGHT,
            "ALT_PLUS": self.ki.ALT_PLUS,
            "ALT_MINUS": self.ki.ALT_MINUS,
            "ALT_LEFT": self.ki.ALT_LEFT,
            "ALT_UP": self.ki.ALT_UP,
            "ALT_DOWN": self.ki.ALT_DOWN,
            "ALT_RIGHT": self.ki.ALT_RIGHT,
            "ALT_0": self.ki.ALT_0,
            "ALT_SQUARE": self.ki.ALT_SQUARE,
            "LNG_LEFT": self.ki.LNG_LEFT,
            "LNG_UP": self.ki.LNG_UP,
            "LNG_DOWN": self.ki.LNG_DOWN,
            "LNG_RIGHT": self.ki.LNG_RIGHT,
            "LNG_SQUARE": self.ki.LNG_SQUARE,
        }

        self.network = sys_utils.Network()

        # Initialize Flask app with absolute template path
        views2_path = os.path.join(os.path.dirname(__file__), "..", "views")
        views2_path = os.path.abspath(views2_path)
        logger.debug(f"Template folder path: {views2_path}")

        app = Flask(__name__, template_folder=views2_path)
        app.secret_key = SESSION_SECRET
        app.config["BABEL_DEFAULT_LOCALE"] = DEFAULT_WEB_LANGUAGE
        app.config["BABEL_TRANSLATION_DIRECTORIES"] = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "locale")
        )
        # Register the custom signed integer converter
        app.url_map.converters["signed_int"] = SignedIntConverter

        logger.info(f"Flask app created successfully: {app}")
        logger.info(f"Template folder: {app.template_folder}")

        # Setup Babel for i18n
        Babel(app, locale_selector=server_locale)  # Picked up by app variable

        # Configure Jinja2 environment for i18n
        app.jinja_env.add_extension("jinja2.ext.i18n")

        app.jinja_env.globals["_"] = gettext

        # # Create a simple gettext function for templates that works without translation files
        # def simple_gettext(text):
        #     return text

        # def simple_ngettext(singular, plural, n):
        #     return singular if n == 1 else plural

        # app.jinja_env.install_gettext_callables(simple_gettext, simple_ngettext, newstyle=True)

        # # Create a context-safe translation function
        # def translate(text):
        #     try:
        #         from flask_babel import gettext
        #         return gettext(text)
        #     except Exception:
        #         return text

        # # Make translation function available to routes
        # app.jinja_env.globals['_'] = translate

        # Static files routes
        @app.route("/images/<path:filename>")
        def send_image(filename):
            return send_file(
                os.path.join(views2_path, "images", filename), mimetype="image/png"
            )

        @app.route("/js/<path:filename>")
        def send_js(filename):
            return send_file(os.path.join(views2_path, "js", filename))

        @app.route("/css/<path:filename>")
        def send_css(filename):
            return send_file(os.path.join(views2_path, "css", filename))

        @app.route("/manifest.webmanifest")
        def send_manifest():
            return send_file(
                os.path.join(views2_path, "manifest.webmanifest"),
                mimetype="application/manifest+json",
            )

        @app.route("/service-worker.js")
        def send_service_worker():
            response = send_file(
                os.path.join(views2_path, "service-worker.js"),
                mimetype="text/javascript",
            )
            response.headers["Service-Worker-Allowed"] = "/"
            return response

        @app.route("/")
        def home():
            # logger.debug("/ called")
            # Get version info
            software_version = "Unknown"
            try:
                with open(self.version_txt, "r") as ver_f:
                    software_version = ver_f.read()
            except (FileNotFoundError, IOError) as e:
                logger.warning(f"Could not read version file: {str(e)}")

            # Try to update GPS state
            try:
                self.update_gps()
            except Exception as e:
                logger.error(f"Failed to update GPS in home route: {str(e)}")

            # Use GPS data if available
            lat_text = str(self.lat) if self.gps_locked else ""
            lon_text = str(self.lon) if self.gps_locked else ""
            gps_icon = "gps_fixed" if self.gps_locked else "gps_off"
            gps_text = gettext("Locked") if self.gps_locked else gettext("Not Locked")

            # Default camera values
            ra_text = "0"
            dec_text = "0"
            camera_icon = "broken_image"

            # Try to get solution data
            try:
                if self.shared_state.solve_state() is True:
                    camera_icon = "camera_alt"
                    solution = self.shared_state.solution()
                    if solution and solution.has_pointing():
                        aligned = solution.pointing.aligned.estimate
                        hh, mm, _ = calc_utils.ra_to_hms(aligned.RA)
                        ra_text = f"{hh:02.0f}h{mm:02.0f}m"
                        dec_text = f"{aligned.Dec: .2f}"
            except Exception as e:
                logger.error(f"Failed to get solution data: {str(e)}")

            # Render the template with available data
            return app.jinja_env.get_template("index.html").render(
                title=gettext("Home"),
                software_version=software_version,
                wifi_mode=self.network.wifi_mode(),
                ip=self.network.local_ip(),
                network_name=self.network.get_connected_ssid(),
                gps_icon=gps_icon,
                gps_text=gps_text,
                lat_text=lat_text,
                lon_text=lon_text,
                camera_icon=camera_icon,
                ra_text=ra_text,
                dec_text=dec_text,
            )

        @app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "POST":
                password = request.form.get("password")
                # Read from hidden form field (set by GET handler); fall back to session
                origin_url = request.form.get("origin_url") or session.get(
                    "origin_url", "/"
                )
                if sys_utils.verify_password("pifinder", password):
                    session["authenticated"] = True
                    session.pop("origin_url", None)
                    return redirect(origin_url)
                else:
                    return app.jinja_env.get_template("login.html").render(
                        title=gettext("Login"),
                        origin_url=origin_url,
                        error_message=gettext("Invalid Password"),
                    )
            else:
                # Prefer ?next= URL param (set by auth_required); fall back to session
                origin_url = request.args.get("next", session.get("origin_url", "/"))
                return app.jinja_env.get_template("login.html").render(
                    title=gettext("Login"), origin_url=origin_url
                )

        @app.route("/remote")
        @auth_required
        def remote():
            return app.jinja_env.get_template("remote.html").render(title=_("Remote"))

        @app.route("/advanced")
        @auth_required
        def advanced():
            return app.jinja_env.get_template("advanced.html").render(
                title=_("Advanced")
            )

        @app.route("/network")
        @auth_required
        def network_page():
            show_new_form = request.args.get("add_new", 0)
            scanned_networks = []
            scan_error = ""
            if show_new_form:
                try:
                    scanned_networks = self.network.scan_wifi_networks()
                except Exception as e:
                    logger.warning("Wi-Fi scan failed: %s", e)
                    scan_error = _("Wi-Fi scan failed")

            return app.jinja_env.get_template("network.html").render(
                title=_("Network"),
                net=self.network,
                show_new_form=show_new_form,
                scanned_networks=scanned_networks,
                scan_error=scan_error,
            )

        @app.route("/gps")
        @auth_required
        def gps_page():
            self.update_gps()
            show_new_form = request.args.get("add_new", 0)
            logger.debug(
                "/gps: %f, %f, %f ",
                self.lat or 0.0,
                self.lon or 0.0,
                self.altitude or 0.0,
            )

            return app.jinja_env.get_template("gps.html").render(
                title=_("GPS"),
                show_new_form=show_new_form,
                lat=self.lat,
                lon=self.lon,
                altitude=self.altitude,
            )

        @app.route("/gps/update", methods=["POST"])
        @auth_required
        def gps_update():
            lat = request.form.get("latitudeDecimal")
            lon = request.form.get("longitudeDecimal")
            altitude = request.form.get("altitude")
            date_req = request.form.get("date")
            time_req = request.form.get("time")
            gps_lock(float(lat), float(lon), float(altitude))
            if time_req and date_req:
                datetime_str = f"{date_req} {time_req}"
                datetime_obj = timez.parse(datetime_str, "%Y-%m-%d %H:%M:%S")
                datetime_utc = datetime_obj.replace(tzinfo=timezone.utc)
                time_lock(datetime_utc)
            logger.debug(
                "GPS update: %s, %s, %s, %s, %s", lat, lon, altitude, date_req, time_req
            )
            time.sleep(1)  # give the gps thread a chance to update
            return redirect("/")

        @app.route("/locations")
        @auth_required
        def locations_page():
            show_new_form = request.args.get("add_new", 0)
            cfg = config.Config()
            cfg.load_config()  # Ensure config is loaded
            return app.jinja_env.get_template("locations.html").render(
                title=_("Locations"),
                locations=cfg.locations.locations,
                show_new_form=show_new_form,
            )

        @app.route("/locations/catalog/countries")
        @auth_required
        def locations_catalog_countries():
            return jsonify({"countries": location_catalog.countries()})

        @app.route("/locations/catalog/regions")
        @auth_required
        def locations_catalog_regions():
            country = request.args.get("country", "")
            return jsonify({"regions": location_catalog.regions(country)})

        @app.route("/locations/catalog/districts")
        @auth_required
        def locations_catalog_districts():
            country = request.args.get("country", "")
            region = request.args.get("region", "")
            return jsonify({"districts": location_catalog.districts(country, region)})

        @app.route("/locations/catalog/places")
        @auth_required
        def locations_catalog_places():
            country = request.args.get("country", "")
            region = request.args.get("region", "")
            district = request.args.get("district", "")
            return jsonify(
                {"places": location_catalog.places(country, region, district)}
            )

        @app.route("/locations/add", methods=["POST"])
        @auth_required
        def location_add():
            try:
                logger.info(
                    "Location add request: name=%r lat=%r lon=%r altitude=%r error=%r source=%r",
                    request.form.get("name"),
                    request.form.get("latitude"),
                    request.form.get("longitude"),
                    request.form.get("altitude"),
                    request.form.get("error_in_m"),
                    request.form.get("source"),
                )
                name = request.form.get("name").strip()
                lat = float(request.form.get("latitude"))
                lon = float(request.form.get("longitude"))
                altitude = float(request.form.get("altitude"))
                error_in_m = float(request.form.get("error_in_m", "0"))
                source = request.form.get("source", "Manual Entry")

                # Server-side validation
                if not name:
                    raise ValueError(_("Location name is required"))
                if not (-90 <= lat <= 90):
                    raise ValueError(_("Latitude must be between -90 and 90"))
                if not (-180 <= lon <= 180):
                    raise ValueError(_("Longitude must be between -180 and 180"))
                if not (-1000 <= altitude <= 10000):
                    raise ValueError(
                        _("Altitude must be between -1000 and 10000 meters")
                    )
                if not (0 <= error_in_m <= 10000):
                    raise ValueError(_("Error must be between 0 and 10000 meters"))

                from PiFinder.locations import Location

                new_location = Location(
                    name=name,
                    latitude=lat,
                    longitude=lon,
                    height=altitude,
                    error_in_m=error_in_m,
                    source=source,
                )

                cfg = config.Config()
                cfg.load_config()
                cfg.locations.add_location(new_location)
                cfg.save_locations()

                self.ui_queue.put("reload_config")
                return redirect("/locations")

            except ValueError as e:
                logger.warning("Location add failed validation: %s", e)
                return app.jinja_env.get_template("locations.html").render(
                    title=_("Locations"),
                    locations=config.Config().locations.locations,
                    show_new_form=1,
                    error_message=str(e),
                )

        @app.route("/locations/rename/<int:location_id>", methods=["POST"])
        @auth_required
        def location_rename(location_id):
            try:
                cfg = config.Config()
                cfg.load_config()

                if not (0 <= location_id < len(cfg.locations.locations)):
                    raise ValueError("Invalid location ID")

                name = request.form.get("name").strip()
                lat = float(request.form.get("latitude"))
                lon = float(request.form.get("longitude"))
                altitude = float(request.form.get("altitude"))
                error_in_m = float(request.form.get("error_in_m", "0"))
                source = request.form.get("source", "Manual Entry")

                # Server-side validation
                if not name:
                    raise ValueError(_("Location name is required"))
                if not (-90 <= lat <= 90):
                    raise ValueError(_("Latitude must be between -90 and 90"))
                if not (-180 <= lon <= 180):
                    raise ValueError(_("Longitude must be between -180 and 180"))
                if not (-1000 <= altitude <= 10000):
                    raise ValueError(
                        _("Altitude must be between -1000 and 10000 meters")
                    )
                if not (0 <= error_in_m <= 10000):
                    raise ValueError(_("Error must be between 0 and 10000 meters"))

                location = cfg.locations.locations[location_id]
                location.name = name
                location.latitude = lat
                location.longitude = lon
                location.height = altitude
                location.error_in_m = error_in_m
                location.source = source

                cfg.save_locations()
                self.ui_queue.put("reload_config")
                return redirect("/locations")

            except ValueError as e:
                return app.jinja_env.get_template("locations.html").render(
                    title=_("Locations"),
                    locations=config.Config().locations.locations,
                    show_new_form=0,
                    error_message=str(e),
                )

        @app.route("/locations/delete/<int:location_id>")
        @auth_required
        def location_delete(location_id):
            cfg = config.Config()
            cfg.load_config()
            if 0 <= location_id < len(cfg.locations.locations):
                location = cfg.locations.locations[location_id]
                cfg.locations.remove_location(location)
                cfg.save_locations()
                # Notify main process to reload config
                self.ui_queue.put("reload_config")
            return redirect("/locations")

        @app.route("/locations/set_default/<int:location_id>")
        @auth_required
        def location_set_default(location_id):
            cfg = config.Config()
            cfg.load_config()
            if 0 <= location_id < len(cfg.locations.locations):
                location = cfg.locations.locations[location_id]
                cfg.locations.set_default(location)
                cfg.save_locations()
                # Notify main process to reload config
                self.ui_queue.put("reload_config")
            return redirect("/locations")

        @app.route("/locations/load/<int:location_id>")
        @auth_required
        def location_load(location_id):
            cfg = config.Config()
            cfg.load_config()  # Ensure config is loaded
            if 0 <= location_id < len(cfg.locations.locations):
                location = cfg.locations.locations[location_id]
                gps_lock(location.latitude, location.longitude, location.height)
            return redirect("/locations")

        @app.route("/network/add", methods=["POST"])
        @auth_required
        def network_add():
            ssid = (request.form.get("ssid") or "").strip()
            scanned_ssid = (request.form.get("ssid_select") or "").strip()
            if not ssid and scanned_ssid != "__manual__":
                ssid = scanned_ssid
            psk = (request.form.get("psk") or "").strip()
            key_mgmt = "WPA-PSK" if psk else "NONE"

            try:
                self.network.add_wifi_network(ssid, key_mgmt, psk)
                return redirect("/network")
            except ValueError as e:
                return app.jinja_env.get_template("network.html").render(
                    title=_("Network"),
                    net=self.network,
                    show_new_form=1,
                    scanned_networks=self.network.scan_wifi_networks(),
                    scan_error="",
                    error_message=str(e),
                )

        @app.route("/network/delete/<int:network_id>")
        @auth_required
        def network_delete(network_id):
            self.network.delete_wifi_network(network_id)
            return redirect("/network")

        @app.route("/network/update", methods=["POST"])
        @auth_required
        def network_update():
            wifi_mode = request.form.get("wifi_mode")
            ap_name = request.form.get("ap_name")
            ap_ip = request.form.get("ap_ip")
            ap_security = request.form.get("ap_security")
            ap_password = request.form.get("ap_password")
            apsta_share_internet = request.form.get("apsta_share_internet") == "1"
            sta_band_preference = request.form.get("sta_band_preference")
            host_name = request.form.get("host_name")

            try:
                self.network.set_ap_name(ap_name)
                self.network.set_ap_ip(ap_ip)
                self.network.set_ap_security(ap_security, ap_password)
                self.network.set_apsta_internet_sharing(apsta_share_internet)
                self.network.set_sta_band_preference(sta_band_preference)
                self.network.set_host_name(host_name)
                self.network.set_wifi_mode(wifi_mode)
                return app.jinja_env.get_template("restart.html").render(
                    title=_("Restart")
                )
            except ValueError as e:
                return app.jinja_env.get_template("network.html").render(
                    title=_("Network"),
                    net=self.network,
                    show_new_form=0,
                    scanned_networks=[],
                    scan_error="",
                    error_message=str(e),
                )

        @app.route("/tools/pwchange", methods=["POST"])
        @auth_required
        def password_change():
            current_password = request.form.get("current_password")
            new_passworda = request.form.get("new_passworda")
            new_passwordb = request.form.get("new_passwordb")

            if new_passworda == "" or current_password == "" or new_passwordb == "":
                return app.jinja_env.get_template("tools.html").render(
                    title=_("Tools"),
                    error_message=_("You must fill in all password fields"),
                )

            if new_passworda == new_passwordb:
                if sys_utils.change_password(
                    "pifinder", current_password, new_passworda
                ):
                    return app.jinja_env.get_template("tools.html").render(
                        title=_("Tools"), status_message=_("Password Changed")
                    )
                else:
                    return app.jinja_env.get_template("tools.html").render(
                        title=_("Tools"), error_message=_("Incorrect current password")
                    )
            else:
                return app.jinja_env.get_template("tools.html").render(
                    title=_("Tools"), error_message=_("New passwords do not match")
                )

        @app.route("/system/restart")
        @auth_required
        def system_restart():
            """
            Restarts the RPI system
            """
            sys_utils.restart_system()
            return "restarting"

        @app.route("/system/restart_pifinder")
        @auth_required
        def pifinder_restart():
            """
            Restarts just the PiFinder software
            """
            sys_utils.restart_pifinder()
            return "restarting"

        @app.route("/equipment")
        @auth_required
        def equipment():
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"), equipment=config.Config().equipment
            )

        @app.route("/equipment/set_active_instrument/<int:instrument_id>")
        @auth_required
        def set_active_instrument(instrument_id: int):
            cfg = config.Config()
            cfg.equipment.set_active_telescope(cfg.equipment.telescopes[instrument_id])
            cfg.save_equipment()
            self.ui_queue.put("reload_config")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=cfg.equipment,
                success_message=cfg.equipment.active_telescope.make
                + " "
                + cfg.equipment.active_telescope.name
                + " "
                + _("set as active instrument."),
            )

        @app.route("/equipment/set_active_eyepiece/<int:eyepiece_id>")
        @auth_required
        def set_active_eyepiece(eyepiece_id: int):
            cfg = config.Config()
            cfg.equipment.set_active_eyepiece(cfg.equipment.eyepieces[eyepiece_id])
            cfg.save_equipment()
            self.ui_queue.put("reload_config")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=cfg.equipment,
                success_message=cfg.equipment.active_eyepiece.make
                + " "
                + cfg.equipment.active_eyepiece.name
                + " "
                + _("set as active eyepiece."),
            )

        @app.route("/equipment/import_from_deepskylog", methods=["POST"])
        @auth_required
        def equipment_import():
            username = request.form.get("dsl_name")
            cfg = config.Config()
            if username:
                instruments = pds.dsl_instruments(username)
                for instrument in instruments:
                    if instrument["type"] == 0:
                        # Skip the naked eye
                        continue

                    make = instrument["instrument_make"]["name"]

                    obstruction_perc = instrument["obstruction_perc"]
                    if obstruction_perc is None:
                        obstruction_perc = 0
                    else:
                        obstruction_perc = float(obstruction_perc)

                    # Convert the html special characters (ampersand, quote, ...) in instrument["name"]
                    # to the corresponding character
                    instrument["name"] = instrument["name"].replace("&amp;", "&")
                    instrument["name"] = instrument["name"].replace("&quot;", '"')
                    instrument["name"] = instrument["name"].replace("&apos;", "'")
                    instrument["name"] = instrument["name"].replace("&lt;", "<")
                    instrument["name"] = instrument["name"].replace("&gt;", ">")

                    new_instrument = Telescope(
                        make=make,
                        name=instrument["name"],
                        aperture_mm=int(instrument["diameter"]),
                        focal_length_mm=int(instrument["diameter"] * instrument["fd"]),
                        obstruction_perc=obstruction_perc,
                        mount_type=instrument["mount_type"]["name"].lower(),
                        flip_image=bool(instrument["flip_image"]),
                        flop_image=bool(instrument["flop_image"]),
                        reverse_arrow_a=False,
                        reverse_arrow_b=False,
                    )
                    try:
                        cfg.equipment.telescopes.index(new_instrument)
                    except ValueError:
                        cfg.equipment.telescopes.append(new_instrument)

                # Add the eyepieces from deepskylog
                eyepieces = pds.dsl_eyepieces(username)
                for eyepiece in eyepieces:
                    # Convert the html special characters (ampersand, quote, ...) in eyepiece["name"]
                    # to the corresponding character
                    eyepiece["name"] = eyepiece["name"].replace("&amp;", "&")
                    eyepiece["name"] = eyepiece["name"].replace("&quot;", '"')
                    eyepiece["name"] = eyepiece["name"].replace("&apos;", "'")
                    eyepiece["name"] = eyepiece["name"].replace("&lt;", "<")
                    eyepiece["name"] = eyepiece["name"].replace("&gt;", ">")

                    make = eyepiece["eyepiece_make"]["name"]

                    new_eyepiece = Eyepiece(
                        make=make,
                        name=eyepiece["name"],
                        focal_length_mm=float(eyepiece["focalLength"]),
                        afov=int(eyepiece["apparentFOV"]),
                        field_stop=float(eyepiece["field_stop_mm"]),
                    )
                    try:
                        cfg.equipment.eyepieces.index(new_eyepiece)
                    except ValueError:
                        cfg.equipment.eyepieces.add_eyepiece(new_eyepiece)

                cfg.save_equipment()
                self.ui_queue.put("reload_config")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=config.Config().equipment,
                success_message=_(
                    "Equipment Imported, restart your PiFinder to use this new data"
                ),
            )

        @app.route("/equipment/edit_eyepiece/<signed_int:eyepiece_id>")
        @auth_required
        def edit_eyepiece(eyepiece_id: int):
            if eyepiece_id >= 0:
                eyepiece = config.Config().equipment.eyepieces[eyepiece_id]
            else:
                eyepiece = Eyepiece(
                    make="", name="", focal_length_mm=0, afov=0, field_stop=0
                )

            return app.jinja_env.get_template("edit_eyepiece.html").render(
                title=_("Edit Eyepiece"), eyepiece=eyepiece, eyepiece_id=eyepiece_id
            )

        @app.route("/equipment/add_eyepiece/<signed_int:eyepiece_id>", methods=["POST"])
        @auth_required
        def equipment_add_eyepiece(eyepiece_id: int):
            cfg = config.Config()

            try:
                make = request.form.get("make") or ""
                name = request.form.get("name") or ""
                focal_length_str = request.form.get("focal_length_mm") or "0"
                afov_str = request.form.get("afov") or "0"
                field_stop_str = request.form.get("field_stop") or "0"

                eyepiece = Eyepiece(
                    make=make,
                    name=name,
                    focal_length_mm=float(focal_length_str),
                    afov=int(afov_str),
                    field_stop=float(field_stop_str),
                )

                if eyepiece_id >= 0:
                    cfg.equipment.update_eyepiece(eyepiece_id, eyepiece)
                else:
                    try:
                        index = cfg.equipment.telescopes.index(eyepiece)
                        cfg.equipment.update_eyepiece(index, eyepiece)
                    except ValueError:
                        cfg.equipment.add_eyepiece(eyepiece)

                cfg.save_equipment()
                self.ui_queue.put("reload_config")
            except Exception as e:
                logger.error(f"Error adding eyepiece: {e}")

            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=config.Config().equipment,
                success_message=_("Eyepiece added, restart your PiFinder to use"),
            )

        @app.route("/equipment/delete_eyepiece/<int:eyepiece_id>")
        @auth_required
        def equipment_delete_eyepiece(eyepiece_id: int):
            cfg = config.Config()
            cfg.equipment.eyepieces.pop(eyepiece_id)
            cfg.save_equipment()
            self.ui_queue.put("reload_config")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=config.Config().equipment,
                success_message=_(
                    "Eyepiece Deleted, restart your PiFinder to remove from menu"
                ),
            )

        @app.route("/equipment/edit_instrument/<signed_int:instrument_id>")
        @auth_required
        def edit_instrument(instrument_id: int):
            if instrument_id >= 0:
                telescope = config.Config().equipment.telescopes[instrument_id]
            else:
                telescope = Telescope(
                    make="",
                    name="",
                    aperture_mm=0,
                    focal_length_mm=0,
                    obstruction_perc=0,
                    mount_type="",
                    flip_image=False,
                    flop_image=False,
                    reverse_arrow_a=False,
                    reverse_arrow_b=False,
                )

            return app.jinja_env.get_template("edit_instrument.html").render(
                title=_("Edit Instrument"),
                telescope=telescope,
                instrument_id=instrument_id,
            )

        @app.route(
            "/equipment/add_instrument/<signed_int:instrument_id>", methods=["POST"]
        )
        @auth_required
        def equipment_add_instrument(instrument_id: int):
            cfg = config.Config()

            try:
                make = request.form.get("make") or ""
                name = request.form.get("name") or ""
                aperture_str = request.form.get("aperture") or "0"
                focal_length_str = request.form.get("focal_length_mm") or "0"
                obstruction_str = request.form.get("obstruction_perc") or "0"
                mount_type = request.form.get("mount_type") or ""

                instrument = Telescope(
                    make=make,
                    name=name,
                    aperture_mm=int(aperture_str),
                    focal_length_mm=int(focal_length_str),
                    obstruction_perc=float(obstruction_str),
                    mount_type=mount_type,
                    flip_image=bool(request.form.get("flip")),
                    flop_image=bool(request.form.get("flop")),
                    reverse_arrow_a=bool(request.form.get("reverse_arrow_a")),
                    reverse_arrow_b=bool(request.form.get("reverse_arrow_b")),
                )
                if instrument_id >= 0:
                    cfg.equipment.telescopes[instrument_id] = instrument
                else:
                    try:
                        index = cfg.equipment.telescopes.index(instrument)
                        cfg.equipment.telescopes[index] = instrument
                    except ValueError:
                        cfg.equipment.telescopes.append(instrument)

                cfg.save_equipment()
                self.ui_queue.put("reload_config")
            except Exception as e:
                logger.error(f"Error adding instrument: {e}")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=config.Config().equipment,
                success_message=_("Instrument Added, restart your PiFinder to use"),
            )

        @app.route("/equipment/delete_instrument/<int:instrument_id>")
        @auth_required
        def equipment_delete_instrument(instrument_id: int):
            cfg = config.Config()
            cfg.equipment.telescopes.pop(instrument_id)
            cfg.save_equipment()
            self.ui_queue.put("reload_config")
            return app.jinja_env.get_template("equipment.html").render(
                title=_("Equipment"),
                equipment=config.Config().equipment,
                success_message=_(
                    "Instrument Deleted, restart your PiFinder to remove from menu"
                ),
            )

        @app.route("/observations")
        @auth_required
        def obs_sessions():
            obs_db = ObservationsDatabase()
            if request.args.get("download", 0) == "1":
                # Download all as TSV
                observations = obs_db.observations_as_tsv()

                response = make_response(observations)
                response.headers["Content-Disposition"] = (
                    "attachment; filename=observations.tsv"
                )
                response.headers["Content-Type"] = "text/tsv"
                return response

            # regular html page of sessions
            sessions = obs_db.get_sessions()
            metadata = {
                "sess_count": len(sessions),
                "object_count": sum(x["observations"] for x in sessions),
                "total_duration": sum(x["duration"] for x in sessions),
            }
            return app.jinja_env.get_template("obs_sessions.html").render(
                title=_("Observations"), sessions=sessions, metadata=metadata
            )

        @app.route("/observations/<session_id>")
        @auth_required
        def obs_session(session_id):
            obs_db = ObservationsDatabase()
            if request.args.get("download", 0) == "1":
                # Download all as TSV
                observations = obs_db.observations_as_tsv(session_id)

                response = make_response(observations)
                response.headers["Content-Disposition"] = (
                    f"attachment; filename=observations_{session_id}.tsv"
                )
                response.headers["Content-Type"] = "text/tsv"
                return response

            session = obs_db.get_sessions(session_id)[0]
            objects = obs_db.get_logs_by_session(session_id)
            ret_objects = []
            for obj in objects:
                obj_ = dict(obj)
                obj_notes = json.loads(obj_["notes"])
                obj_["notes"] = "<br>".join(
                    [f"{key}: {value}" for key, value in obj_notes.items()]
                )
                ret_objects.append(obj_)
            return app.jinja_env.get_template("obs_session_log.html").render(
                title=_("Session Log"), session=session, objects=ret_objects
            )

        @app.route("/tools")
        @auth_required
        def tools():
            return app.jinja_env.get_template("tools.html").render(title=_("Tools"))

        @app.route("/livecam")
        @auth_required
        def livecam():
            if hasattr(self.shared_state, "set_livecam_settings"):
                self.shared_state.set_livecam_settings(
                    settings_from_config(config.Config())
                )
            return app.jinja_env.get_template("livecam.html").render(title=_("LiveCam"))

        def _indi_json_response(ok=True, message="", error=""):
            status = 200 if ok else 400
            return jsonify({"ok": ok, "message": message, "error": error}), status

        def _indi_config_values():
            cfg = config.Config()
            cfg.load_config()
            profile_info = sys_utils.get_indi_profile_drivers()
            device_name = sys_utils.get_indi_profile_device_name()
            return {
                "indi_profile_name": profile_info.get("profile", ""),
                "indi_profile_drivers": profile_info.get("drivers", []),
                "device_name": device_name,
                "is_onstepx_driver": sys_utils.is_onstepx_device_name(device_name),
                "connection_type": cfg.get_option("onstep_connection_type", "network"),
                "network_host": cfg.get_option("onstep_network_host", ""),
                "network_port": int(cfg.get_option("onstep_network_port", 9999)),
                "serial_port": cfg.get_option("onstep_serial_port", ""),
                "server_host": cfg.get_option("mount_control_indi_host", "localhost"),
                "server_port": int(cfg.get_option("mount_control_indi_port", 7624)),
                "mount_type": cfg.get_option("mount_type", "Alt/Az"),
                "skysafari_imu_align_without_solve": bool(
                    cfg.get_option("skysafari_imu_align_without_solve", True)
                ),
                "skysafari_lx200_mount_code": cfg.get_option(
                    "skysafari_lx200_mount_code", "auto"
                ),
                "skysafari_indi_goto": bool(
                    cfg.get_option("skysafari_indi_goto", False)
                ),
                "skysafari_indi_sync": bool(
                    cfg.get_option("skysafari_indi_sync", False)
                ),
                "indi_goto_refine_once": bool(
                    cfg.get_option("indi_goto_refine_once", False)
                ),
                "indi_goto_refine_accuracy_arcmin": float(
                    cfg.get_option("indi_goto_refine_accuracy_arcmin", 10.0)
                ),
                "indi_goto_method": cfg.get_option(
                    "indi_goto_method", "indi_mount"
                ),
                "indi_tracking_guide_enabled": bool(
                    cfg.get_option("indi_tracking_guide_enabled", False)
                ),
                "indi_tracking_guide_goto_recovery_enabled": bool(
                    cfg.get_option("indi_tracking_guide_goto_recovery_enabled", False)
                ),
            }

        def _onstep_property_name(property_name, indi_cfg=None):
            device_name = (
                indi_cfg.get("device_name")
                if indi_cfg is not None
                else sys_utils.get_indi_profile_device_name()
            )
            return f"{device_name}.{property_name}"

        def _onstep_property_on(property_name, indi_cfg=None):
            return f"{_onstep_property_name(property_name, indi_cfg)}=On"

        def _require_onstepx_driver(indi_cfg):
            if not indi_cfg["is_onstepx_driver"]:
                raise ValueError(
                    _(
                        "This INDI profile uses %(driver)s. OnStepX controls are "
                        "available only when the active profile driver is LX200 OnStepX."
                    )
                    % {"driver": indi_cfg["device_name"] or _("unknown driver")}
                )

        web_motion_lock = threading.Lock()
        web_motion_timer = {"timer": None, "token": 0}

        def _cancel_web_motion_timer():
            with web_motion_lock:
                timer = web_motion_timer.get("timer")
                web_motion_timer["timer"] = None
                web_motion_timer["token"] += 1
            if timer is not None:
                timer.cancel()

        def _abort_web_motion_if_current(token):
            with web_motion_lock:
                if token != web_motion_timer["token"]:
                    return
                web_motion_timer["timer"] = None

            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                result = sys_utils.apply_indi_onstep_properties(
                    [_onstep_property_on("TELESCOPE_ABORT_MOTION.ABORT", indi_cfg)],
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                )
                if result.get("ok"):
                    logger.warning("Web INDI manual motion lease expired; stop sent")
                else:
                    logger.warning(
                        "Web INDI manual motion timeout stop failed: %s",
                        result.get("stderr") or result.get("stdout"),
                    )
            except Exception:
                logger.exception("Web INDI manual motion timeout stop failed")

        def _schedule_web_motion_timer():
            with web_motion_lock:
                timer = web_motion_timer.get("timer")
                if timer is not None:
                    timer.cancel()
                web_motion_timer["token"] += 1
                token = web_motion_timer["token"]
                timer = threading.Timer(
                    WEB_MOTION_LEASE_SECONDS,
                    _abort_web_motion_if_current,
                    args=(token,),
                )
                timer.daemon = True
                web_motion_timer["timer"] = timer
                timer.start()

        def _pifinder_location_time_values():
            source = "Current time"
            lat = lon = elev = ""
            location_locked = False
            source_type = "none"
            try:
                location = self.shared_state.location()
            except Exception:
                location = None

            if location and getattr(location, "lock", False):
                lat = location.lat
                lon = location.lon
                elev = location.altitude if location.altitude is not None else 0
                source = "GPS / loaded location"
                source_type = "gps_locked"
                location_locked = True
            else:
                cfg = config.Config()
                cfg.load_config()
                default_location = cfg.locations.default_location
                if default_location:
                    lat = default_location.latitude
                    lon = default_location.longitude
                    elev = default_location.height
                    source = f"Default location fallback: {default_location.name}"
                    source_type = "default_location"

            return {
                "latitude": lat,
                "longitude": lon,
                "elevation": elev,
                "utc_time": _current_pifinder_utc_datetime()
                .replace(microsecond=0)
                .strftime("%Y-%m-%dT%H:%M:%S"),
                "source": source,
                "source_type": source_type,
                "location_locked": location_locked,
                "lock_status": "Locked" if location_locked else "Not locked",
            }

        def _current_pifinder_utc_datetime():
            try:
                pifinder_dt = self.shared_state.datetime()
            except Exception:
                logger.exception("Could not read PiFinder shared datetime")
                pifinder_dt = None

            if pifinder_dt is None:
                return datetime.now(timezone.utc)
            return sys_utils.parse_indi_utc_datetime(pifinder_dt)

        def _onstep_location_display(onstep_props):
            return sys_utils.format_onstep_location_display_with_cache(onstep_props)

        def _onstep_effective_location(onstep_props):
            return sys_utils.effective_onstep_location(onstep_props)

        def _onstep_effective_location_display(onstep_props):
            return sys_utils.format_effective_onstep_location(onstep_props)

        def _onstep_location_matches(
            onstep_props,
            latitude,
            longitude,
            tolerance=sys_utils.ONSTEP_LOCATION_READBACK_TOLERANCE_DEGREES,
            indi_cfg=None,
        ):
            return sys_utils.onstep_location_readback_matches(
                onstep_props.get(
                    _onstep_property_name("GEOGRAPHIC_COORD.LAT", indi_cfg)
                ),
                onstep_props.get(
                    _onstep_property_name("GEOGRAPHIC_COORD.LONG", indi_cfg)
                ),
                latitude,
                longitude,
                tolerance_degrees=tolerance,
            )

        def _get_indi_onstep_properties(indi_cfg):
            return sys_utils.get_indi_onstep_properties(
                server_host=indi_cfg["server_host"],
                server_port=indi_cfg["server_port"],
                device_name=indi_cfg["device_name"],
            )

        def _mount_control_status():
            try:
                with open(
                    utils.data_dir / "mount_control_status.json",
                    encoding="utf-8",
                ) as status_in:
                    return json.load(status_in)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return {}

        def _goto_guide_status():
            try:
                with open(
                    utils.data_dir / "indi_goto_guide_status.json",
                    encoding="utf-8",
                ) as status_in:
                    return json.load(status_in)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return {}

        def _write_backlash_stop_request():
            utils.create_path(utils.data_dir)
            payload = {"requested_at": time.time(), "source": "web"}
            tmp_path = WEB_BACKLASH_STOP_REQUEST_FILE.with_name(
                f"{WEB_BACKLASH_STOP_REQUEST_FILE.name}.{os.getpid()}.tmp"
            )
            with open(tmp_path, "w", encoding="utf-8") as stop_out:
                json.dump(payload, stop_out)
                stop_out.flush()
                os.fsync(stop_out.fileno())
            tmp_path.replace(WEB_BACKLASH_STOP_REQUEST_FILE)

        def _parse_backlash_value(value):
            try:
                if value in (None, ""):
                    return ""
                return int(float(value))
            except (TypeError, ValueError):
                return ""

        def _onstep_first_property_value(onstep_props, property_names, indi_cfg):
            for property_name in property_names:
                value = onstep_props.get(_onstep_property_name(property_name, indi_cfg))
                if value not in (None, ""):
                    return value
            return None

        def _onstep_backlash_values(onstep_props, indi_cfg):
            return {
                "ra": _parse_backlash_value(
                    _onstep_first_property_value(
                        onstep_props,
                        sys_utils.ONSTEP_BACKLASH_RA_FALLBACK_PROPERTIES,
                        indi_cfg,
                    )
                ),
                "de": _parse_backlash_value(
                    _onstep_first_property_value(
                        onstep_props,
                        sys_utils.ONSTEP_BACKLASH_DE_FALLBACK_PROPERTIES,
                        indi_cfg,
                    )
                ),
            }

        def _onstep_mount_state(onstep_props, indi_cfg):
            return sys_utils.parse_onstep_home_park_state(
                status_text=onstep_props.get(
                    _onstep_property_name("OnStep Status.Park", indi_cfg)
                ),
                park_switch=onstep_props.get(
                    _onstep_property_name("TELESCOPE_PARK.PARK", indi_cfg)
                ),
                unpark_switch=onstep_props.get(
                    _onstep_property_name("TELESCOPE_PARK.UNPARK", indi_cfg)
                ),
                raw_status=onstep_props.get(
                    _onstep_property_name("OnStep Status.:GU# return", indi_cfg)
                ),
            )

        def _validate_backlash_form_value(name):
            try:
                value = int(float(request.form.get(name) or "0"))
            except ValueError as exc:
                raise ValueError(_("Backlash must be a number")) from exc
            if not WEB_BACKLASH_MIN_VALUE <= value <= WEB_BACKLASH_MAX_VALUE:
                raise ValueError(
                    _("Backlash must be between 0 and %(max)d")
                    % {"max": WEB_BACKLASH_MAX_VALUE}
                )
            return value

        def _multipoint_align_status():
            status = _mount_control_status()
            align_status = status.get("multipoint_align")
            return align_status if isinstance(align_status, dict) else {}

        def _queue_multipoint_align_command(command):
            if self.mountcontrol_queue is None:
                raise RuntimeError(_("Mount-control process is not available"))
            self.mountcontrol_queue.put(command)

        def _wait_for_onstep_location_match(indi_cfg, latitude, longitude, timeout=5.0):
            deadline = time.monotonic() + timeout
            onstep_props = {}
            while True:
                onstep_props = _get_indi_onstep_properties(indi_cfg)
                if _onstep_location_matches(
                    onstep_props,
                    latitude,
                    longitude,
                    indi_cfg=indi_cfg,
                ):
                    return onstep_props
                if time.monotonic() >= deadline:
                    return onstep_props
                time.sleep(0.5)

        def _render_indi_page(status_message="", error_message="", onstep_props=None):
            indi_cfg = _indi_config_values()

            try:
                ap_clients = self.network.get_ap_clients()
            except Exception:
                logger.exception("Could not get AP clients for INDI setup")
                ap_clients = []

            if onstep_props is None:
                onstep_props = _get_indi_onstep_properties(indi_cfg)
            backlash_values = _onstep_backlash_values(onstep_props, indi_cfg)
            return app.jinja_env.get_template("indi_mount.html").render(
                title=_("INDI"),
                **indi_cfg,
                serial_ports=sys_utils.list_onstep_serial_ports(),
                ap_clients=ap_clients,
                onstep_props=onstep_props,
                onstep_device_name=indi_cfg["device_name"],
                onstep_location_display=_onstep_location_display(onstep_props),
                onstep_effective_location_display=_onstep_effective_location_display(
                    onstep_props
                ),
                onstep_effective_location=_onstep_effective_location(onstep_props),
                onstep_mount_state=_onstep_mount_state(onstep_props, indi_cfg),
                pifinder_location_time=_pifinder_location_time_values(),
                backlash_values=backlash_values,
                align_stars=BRIGHT_ALIGN_STARS,
                multipoint_align=_multipoint_align_status(),
                mount_control_status=_mount_control_status(),
                goto_guide_status=_goto_guide_status(),
                web_motion_keepalive_ms=int(WEB_MOTION_KEEPALIVE_INTERVAL * 1000),
                slew_rate_labels=[
                    "Off",
                    "1/2x - VSlow",
                    "1x - Slow",
                    "2x",
                    "4x",
                    "8x - Center",
                    "20x - Find",
                    "48x - Fast",
                    "1/2 Max - VFast",
                    "Max",
                ],
                status_message=status_message,
                error_message=error_message,
            )

        @app.route("/indi")
        @auth_required
        def indi_page():
            return _render_indi_page()

        @app.route("/tools/indi_mount")
        @auth_required
        def indi_mount_setup_redirect():
            return redirect("/indi")

        @app.route("/indi/current_values")
        @auth_required
        def indi_current_values():
            indi_cfg = _indi_config_values()
            onstep_props = _get_indi_onstep_properties(indi_cfg)
            return jsonify(
                {
                    "ok": True,
                    "mount_type": indi_cfg["mount_type"],
                    "onstep_device_name": indi_cfg["device_name"],
                    "is_onstepx_driver": indi_cfg["is_onstepx_driver"],
                    "pifinder_location_time": _pifinder_location_time_values(),
                    "onstep_props": onstep_props,
                    "onstep_location_display": _onstep_location_display(onstep_props),
                    "onstep_effective_location": _onstep_effective_location(
                        onstep_props
                    ),
                    "onstep_effective_location_display": (
                        _onstep_effective_location_display(onstep_props)
                    ),
                    "onstep_mount_state": _onstep_mount_state(onstep_props, indi_cfg),
                    "backlash_values": _onstep_backlash_values(onstep_props, indi_cfg),
                    "align_stars": BRIGHT_ALIGN_STARS,
                    "multipoint_align": _multipoint_align_status(),
                    "mount_control_status": _mount_control_status(),
                    "goto_guide_status": _goto_guide_status(),
                }
            )

        @app.route("/indi/skysafari", methods=["POST"])
        @auth_required
        def indi_skysafari_update():
            mount_code = (
                request.form.get("skysafari_lx200_mount_code") or "auto"
            ).strip()
            if mount_code != "auto":
                mount_code = mount_code.upper()
            if mount_code not in ("auto", "A", "P", "G"):
                return _render_indi_page(
                    error_message=_("Invalid SkySafari mount status code")
                )

            try:
                refine_accuracy_arcmin = float(
                    request.form.get("indi_goto_refine_accuracy_arcmin") or "10.0"
                )
                if refine_accuracy_arcmin <= 0:
                    raise ValueError("Refine accuracy must be greater than zero")

                cfg = config.Config()
                cfg.load_config()
                cfg.set_option(
                    "skysafari_imu_align_without_solve",
                    request.form.get("skysafari_imu_align_without_solve") == "on",
                )
                cfg.set_option("skysafari_lx200_mount_code", mount_code)
                cfg.set_option(
                    "skysafari_indi_goto",
                    request.form.get("skysafari_indi_goto") == "on",
                )
                cfg.set_option(
                    "skysafari_indi_sync",
                    request.form.get("skysafari_indi_sync") == "on",
                )
                cfg.set_option(
                    "indi_goto_refine_once",
                    request.form.get("indi_goto_refine_once") == "on",
                )
                cfg.set_option(
                    "indi_goto_refine_accuracy_arcmin", refine_accuracy_arcmin
                )
                self.ui_queue.put("reload_config")
                return _render_indi_page(_("SkySafari mount settings applied"))
            except ValueError as e:
                logger.warning("Could not apply SkySafari mount settings: %s", e)
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/goto_guide", methods=["POST"])
        @auth_required
        def indi_goto_guide_update():
            goto_method = (request.form.get("indi_goto_method") or "").strip()
            if goto_method not in ("indi_mount", "pifinder"):
                return _render_indi_page(
                    error_message=_("Invalid INDI GoTo method")
                )

            cfg = config.Config()
            cfg.load_config()
            cfg.set_option("indi_goto_method", goto_method)
            cfg.set_option(
                "indi_tracking_guide_enabled",
                request.form.get("indi_tracking_guide_enabled") == "on",
            )
            cfg.set_option(
                "indi_tracking_guide_goto_recovery_enabled",
                request.form.get("indi_tracking_guide_goto_recovery_enabled") == "on",
            )
            self.ui_queue.put("reload_config")
            return _render_indi_page(_("INDI GoTo / Guide settings applied"))

        @app.route("/indi/driver", methods=["POST"])
        @auth_required
        def indi_mount_update():
            connection_type = (request.form.get("connection_type") or "network").strip()
            serial_port = (request.form.get("serial_port") or "").strip()
            serial_manual = (request.form.get("serial_manual") or "").strip()
            network_host = (request.form.get("network_host") or "").strip()
            network_manual = (request.form.get("network_manual") or "").strip()
            server_host = (request.form.get("server_host") or "localhost").strip()

            if serial_port == "__manual__":
                serial_port = serial_manual
            if network_host == "__manual__":
                network_host = network_manual

            try:
                network_port = int(request.form.get("network_port") or "9999")
                server_port = int(request.form.get("server_port") or "7624")
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                result = sys_utils.apply_indi_onstep_connection(
                    connection_type=connection_type,
                    serial_port=serial_port,
                    network_host=network_host,
                    network_port=network_port,
                    server_host=server_host,
                    server_port=server_port,
                    device_name=indi_cfg["device_name"],
                )
                if not result["ok"]:
                    raise RuntimeError(
                        result.get("stderr")
                        or result.get("stdout")
                        or "INDI setting command failed"
                    )

                cfg = config.Config()
                cfg.load_config()
                cfg.set_option("onstep_connection_type", connection_type)
                cfg.set_option("onstep_serial_port", serial_port)
                cfg.set_option("onstep_network_host", network_host)
                cfg.set_option("onstep_network_port", network_port)
                cfg.set_option("mount_control_indi_host", server_host)
                cfg.set_option("mount_control_indi_port", server_port)
                self.ui_queue.put("reload_config")
                return _render_indi_page(_("INDI OnStep settings applied"))
            except (RuntimeError, ValueError) as e:
                logger.warning("Could not apply INDI OnStep settings: %s", e)
                return _render_indi_page(error_message=str(e))

        def _apply_indi_action(properties, success_message):
            indi_cfg = _indi_config_values()
            result = sys_utils.apply_indi_onstep_properties(
                properties,
                server_host=indi_cfg["server_host"],
                server_port=indi_cfg["server_port"],
            )
            if not result["ok"]:
                raise RuntimeError(
                    result.get("stderr")
                    or result.get("stdout")
                    or "INDI command failed"
                )
            return _render_indi_page(success_message)

        def _apply_indi_action_json(properties, success_message):
            indi_cfg = _indi_config_values()
            result = sys_utils.apply_indi_onstep_properties(
                properties,
                server_host=indi_cfg["server_host"],
                server_port=indi_cfg["server_port"],
            )
            if not result["ok"]:
                raise RuntimeError(
                    result.get("stderr")
                    or result.get("stdout")
                    or "INDI command failed"
                )
            return _indi_json_response(message=success_message)

        @app.route("/indi/restart", methods=["POST"])
        @auth_required
        def indi_restart():
            try:
                result = sys_utils.restart_indi_web_manager()
                if not result["ok"]:
                    raise RuntimeError(
                        result.get("stderr")
                        or result.get("stdout")
                        or "Could not restart INDI Web Manager"
                    )
                time.sleep(3.0)
                indi_cfg = _indi_config_values()
                connect_result = sys_utils.connect_indi_onstep_driver(
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                    device_name=indi_cfg["device_name"],
                )
                if not connect_result["ok"]:
                    raise RuntimeError(
                        connect_result.get("stderr")
                        or connect_result.get("stdout")
                        or "Could not connect INDI OnStep driver"
                    )
                message = _("INDI server restarted and driver connected")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(message=message)
                return _render_indi_page(message)
            except RuntimeError as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(ok=False, error=str(e))
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/park", methods=["POST"])
        @auth_required
        def indi_park():
            action = (request.form.get("park_action") or "").strip()
            indi_cfg = _indi_config_values()
            action_map = {
                "PARK": [_onstep_property_on("TELESCOPE_PARK.PARK", indi_cfg)],
                "UNPARK": [_onstep_property_on("TELESCOPE_PARK.UNPARK", indi_cfg)],
                "SET_HOME": [_onstep_property_on("TELESCOPE_HOME.SET", indi_cfg)],
                "RETURN_HOME": [_onstep_property_on("TELESCOPE_HOME.GO", indi_cfg)],
                "SET_PARK": [
                    _onstep_property_on("TELESCOPE_PARK_OPTION.PARK_CURRENT", indi_cfg)
                ],
            }
            try:
                _require_onstepx_driver(indi_cfg)
                if action not in action_map:
                    raise ValueError("Invalid park action")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _apply_indi_action_json(
                        action_map[action],
                        f"{action.replace('_', ' ').title()} command sent",
                    )
                return _apply_indi_action(
                    action_map[action],
                    _(f"{action.replace('_', ' ').title()} command sent"),
                )
            except (RuntimeError, ValueError) as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(ok=False, error=str(e))
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/slew_rate", methods=["POST"])
        @auth_required
        def indi_slew_rate():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                rate = int(request.form.get("slew_rate") or "6")
                if not 0 <= rate <= 9:
                    raise ValueError("Slew rate must be between 0 and 9")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _apply_indi_action_json(
                        [_onstep_property_on(f"TELESCOPE_SLEW_RATE.{rate}", indi_cfg)],
                        f"Slew rate {rate} selected",
                    )
                return _apply_indi_action(
                    [_onstep_property_on(f"TELESCOPE_SLEW_RATE.{rate}", indi_cfg)],
                    _(f"Slew rate {rate} selected"),
                )
            except (RuntimeError, ValueError) as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(ok=False, error=str(e))
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/motion", methods=["POST"])
        @auth_required
        def indi_motion():
            direction = (request.form.get("direction") or "").strip().lower()
            keepalive = request.form.get("keepalive") in {"1", "true", "yes"}
            indi_cfg = _indi_config_values()
            motion_map = {
                "north": _onstep_property_on(
                    "TELESCOPE_MOTION_NS.MOTION_NORTH", indi_cfg
                ),
                "south": _onstep_property_on(
                    "TELESCOPE_MOTION_NS.MOTION_SOUTH", indi_cfg
                ),
                "west": _onstep_property_on(
                    "TELESCOPE_MOTION_WE.MOTION_EAST", indi_cfg
                ),
                "east": _onstep_property_on(
                    "TELESCOPE_MOTION_WE.MOTION_WEST", indi_cfg
                ),
                "northeast": [
                    _onstep_property_on("TELESCOPE_MOTION_NS.MOTION_NORTH", indi_cfg),
                    _onstep_property_on("TELESCOPE_MOTION_WE.MOTION_WEST", indi_cfg),
                ],
                "northwest": [
                    _onstep_property_on("TELESCOPE_MOTION_NS.MOTION_NORTH", indi_cfg),
                    _onstep_property_on("TELESCOPE_MOTION_WE.MOTION_EAST", indi_cfg),
                ],
                "southeast": [
                    _onstep_property_on("TELESCOPE_MOTION_NS.MOTION_SOUTH", indi_cfg),
                    _onstep_property_on("TELESCOPE_MOTION_WE.MOTION_WEST", indi_cfg),
                ],
                "southwest": [
                    _onstep_property_on("TELESCOPE_MOTION_NS.MOTION_SOUTH", indi_cfg),
                    _onstep_property_on("TELESCOPE_MOTION_WE.MOTION_EAST", indi_cfg),
                ],
                "stop": _onstep_property_on("TELESCOPE_ABORT_MOTION.ABORT", indi_cfg),
            }
            try:
                _require_onstepx_driver(indi_cfg)
                if direction not in motion_map:
                    raise ValueError("Invalid motion command")
                if keepalive:
                    if direction == "stop":
                        _cancel_web_motion_timer()
                    else:
                        _schedule_web_motion_timer()
                    return _indi_json_response(message="Motion keepalive")

                properties = motion_map[direction]
                if isinstance(properties, str):
                    properties = [properties]
                result = sys_utils.apply_indi_onstep_properties(
                    properties,
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                )
                if not result.get("ok"):
                    error = (
                        result.get("stderr")
                        or result.get("stdout")
                        or "INDI command failed"
                    )
                    raise RuntimeError(error)
                if direction != "stop":
                    _schedule_web_motion_timer()
                else:
                    _cancel_web_motion_timer()
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(message="Motion command sent")
                return _render_indi_page(_("Motion command sent"))
            except (RuntimeError, ValueError) as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(ok=False, error=str(e))
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/location_time", methods=["POST"])
        @auth_required
        def indi_location_time():
            try:
                lat = float(request.form.get("latitude") or "0")
                lon = float(request.form.get("longitude") or "0")
                elev = float(request.form.get("elevation") or "0")
                if not -90 <= lat <= 90:
                    raise ValueError("Latitude must be between -90 and 90")
                if not -180 <= lon <= 180:
                    raise ValueError("Longitude must be between -180 and 180")
                utc_time = _current_pifinder_utc_datetime()
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                sync_result = sys_utils.apply_indi_onstep_location_time(
                    latitude=lat,
                    longitude=lon,
                    elevation=elev,
                    utc_datetime=utc_time,
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                    device_name=indi_cfg["device_name"],
                )
                if not sync_result.get("ok"):
                    raise RuntimeError(
                        "INDI OnStep location/time sync failed: "
                        + (
                            sync_result.get("stderr")
                            or sync_result.get("stdout")
                            or "unknown error"
                        )
                    )

                onstep_props = _wait_for_onstep_location_match(
                    indi_cfg,
                    lat,
                    lon,
                    timeout=8.0,
                )
                if not _onstep_location_matches(
                    onstep_props,
                    lat,
                    lon,
                    indi_cfg=indi_cfg,
                ):
                    logger.warning(
                        "Direct LX200 OnStep sync completed, but INDI readback "
                        "does not match requested location: lat=%s lon=%s props=%s",
                        lat,
                        lon,
                        onstep_props,
                    )
                sys_utils.write_onstep_location_cache(lat, lon, elev, utc_time)

                return _render_indi_page(
                    _("Location and UTC time sent via INDI"),
                    onstep_props=onstep_props,
                )
            except (RuntimeError, ValueError) as e:
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/multipoint_align", methods=["POST"])
        @auth_required
        def indi_multipoint_align():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                action = (request.form.get("align_action") or "start").strip().lower()

                if action == "start":
                    mode = (request.form.get("align_mode") or "manual").strip().lower()
                    if mode not in {"manual", "auto"}:
                        raise ValueError(_("Invalid alignment mode"))
                    points = clamp_align_points(request.form.get("align_points"))
                    star_name = (request.form.get("align_star") or "").strip()
                    if mode == "manual" and not get_align_star(star_name):
                        raise ValueError(_("Select a valid alignment star"))
                    _queue_multipoint_align_command(
                        {
                            "type": "multipoint_align_start",
                            "mode": mode,
                            "points": points,
                            "star_name": star_name if mode == "manual" else "",
                        }
                    )
                    message = _("Multi-point alignment started")
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return _indi_json_response(message=message)
                    return _render_indi_page(message)

                if action == "select_star":
                    star_name = (request.form.get("align_star") or "").strip()
                    if not get_align_star(star_name):
                        raise ValueError(_("Select a valid alignment star"))
                    _queue_multipoint_align_command(
                        {
                            "type": "multipoint_align_select_star",
                            "star_name": star_name,
                            "goto": True,
                        }
                    )
                    message = _("Alignment star GoTo requested")
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return _indi_json_response(message=message)
                    return _render_indi_page(message)

                if action == "confirm":
                    _queue_multipoint_align_command(
                        {"type": "multipoint_align_confirm", "source": "web"}
                    )
                    message = _("Alignment point confirmed")
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return _indi_json_response(message=message)
                    return _render_indi_page(message)

                if action == "cancel":
                    _queue_multipoint_align_command({"type": "multipoint_align_cancel"})
                    message = _("Multi-point alignment cancelled")
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return _indi_json_response(message=message)
                    return _render_indi_page(message)

                raise ValueError(_("Invalid alignment action"))
            except (RuntimeError, ValueError) as e:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return _indi_json_response(ok=False, error=str(e))
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/backlash", methods=["POST"])
        @auth_required
        def indi_backlash_save():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                backlash_ra = _validate_backlash_form_value("backlash_ra")
                backlash_de = _validate_backlash_form_value("backlash_de")
                result = sys_utils.apply_indi_onstep_backlash(
                    backlash_ra,
                    backlash_de,
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                    device_name=indi_cfg["device_name"],
                )
                if not result.get("ok"):
                    raise RuntimeError(
                        result.get("stderr")
                        or result.get("stdout")
                        or "INDI backlash command failed"
                    )
                if self.mountcontrol_queue is not None:
                    self.mountcontrol_queue.put({"type": "refresh_backlash"})
                return _render_indi_page(_("Backlash settings saved"))
            except (RuntimeError, ValueError) as e:
                return _render_indi_page(error_message=str(e))

        @app.route("/indi/backlash/auto", methods=["POST"])
        @auth_required
        def indi_backlash_auto():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                mode = (
                    request.form.get("mode") or "compass_goto_loop"
                ).strip().lower()
                if mode != "compass_goto_loop":
                    raise ValueError(_("Invalid backlash auto mode"))
                repeats_raw = request.form.get(
                    "repeats", str(WEB_BACKLASH_DEFAULT_REPEATS)
                )
                try:
                    repeats = int(repeats_raw)
                except (TypeError, ValueError):
                    raise ValueError(_("Motion test repeats must be a number"))
                if not (
                    WEB_BACKLASH_MIN_REPEATS
                    <= repeats
                    <= WEB_BACKLASH_MAX_REPEATS
                ):
                    raise ValueError(
                        _("Motion test repeats must be between 1 and %(max)d")
                        % {"max": WEB_BACKLASH_MAX_REPEATS}
                    )
                if self.mountcontrol_queue is None:
                    raise RuntimeError(_("Mount-control process is not available"))
                self.mountcontrol_queue.put(
                    {"type": "auto_backlash", "mode": mode, "repeats": repeats}
                )
                return _indi_json_response(
                    message=_("Solved GoTo motion test started")
                )
            except (RuntimeError, ValueError) as e:
                return _indi_json_response(ok=False, error=str(e))

        @app.route("/indi/backlash/auto/continue", methods=["POST"])
        @auth_required
        def indi_backlash_auto_continue():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                if self.mountcontrol_queue is None:
                    raise RuntimeError(_("Mount-control process is not available"))
                self.mountcontrol_queue.put({"type": "backlash_compass_continue"})
                return _indi_json_response(
                    message=_("Solved GoTo loop continue requested")
                )
            except (RuntimeError, ValueError) as e:
                return _indi_json_response(ok=False, error=str(e))

        @app.route("/indi/backlash/auto/stop", methods=["POST"])
        @auth_required
        def indi_backlash_auto_stop():
            try:
                indi_cfg = _indi_config_values()
                _require_onstepx_driver(indi_cfg)
                _write_backlash_stop_request()
                if self.mountcontrol_queue is not None:
                    self.mountcontrol_queue.put({"type": "backlash_compass_stop"})

                result = sys_utils.apply_indi_onstep_properties(
                    [
                        _onstep_property_on("TELESCOPE_ABORT_MOTION.ABORT", indi_cfg),
                        _onstep_property_on("TELESCOPE_TRACK_STATE.TRACK_OFF", indi_cfg),
                    ],
                    server_host=indi_cfg["server_host"],
                    server_port=indi_cfg["server_port"],
                )
                message = _("Backlash motion test stop requested")
                if not result.get("ok"):
                    logger.warning(
                        "Backlash stop requested, but immediate INDI stop failed: %s",
                        result.get("stderr") or result.get("stdout") or result,
                    )
                    message = _(
                        "Backlash stop requested; waiting for mount-control process stop"
                    )
                return _indi_json_response(message=message)
            except (RuntimeError, ValueError, OSError) as e:
                return _indi_json_response(ok=False, error=str(e))

        @app.route("/logs")
        @auth_required
        def logs_page():
            return app.jinja_env.get_template("logs.html").render(title=_("Logs"))

        @app.route("/logs/stream")
        @auth_required
        def stream_logs():
            import time

            TAIL_BYTES = 100 * 1024  # serve only the last 100 KB on first load
            t0 = time.monotonic()
            try:
                position = int(request.args.get("position", 0))
                log_file = os.path.expanduser("~/PiFinder_data/pifinder.log")

                try:
                    file_size = os.path.getsize(log_file)
                    logs_logger.debug(
                        "stream_logs: position=%d file_size=%d", position, file_size
                    )

                    # Reset when file shrank (rotation) or on first call; tail large files.
                    if position > file_size or position == 0:
                        position = max(0, file_size - TAIL_BYTES)

                    t1 = time.monotonic()
                    with open(log_file, "r") as f:
                        f.seek(position)
                        new_lines = f.readlines()
                        new_position = f.tell()
                    logs_logger.debug(
                        "stream_logs: read %d lines (%d bytes) in %.3fs",
                        len(new_lines),
                        new_position - position,
                        time.monotonic() - t1,
                    )

                    if new_position - position > 1024 * 1024:
                        logs_logger.warning(
                            "stream_logs: large response %.1f MB",
                            (new_position - position) / 1e6,
                        )

                    if new_lines:
                        return jsonify({"logs": new_lines, "position": new_position})
                    else:
                        return jsonify({"logs": [], "position": new_position})
                except FileNotFoundError:
                    logger.error(f"Log file not found: {log_file}")
                    return jsonify({"logs": [], "position": 0, "file_not_found": True})

            except Exception as e:
                logger.error(f"Error streaming logs: {e}")
                return jsonify({"logs": [], "position": position})
            finally:
                logger.debug("stream_logs: total %.3fs", time.monotonic() - t0)

        @app.route("/logs/download")
        @auth_required
        def download_logs():
            import zipfile
            import tempfile

            try:
                # Create a temporary zip file
                timestamp = timez.local_now().strftime("%Y%m%d_%H%M%S")

                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".zip"
                ) as temp_file:
                    zip_path = temp_file.name

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    # Add all log files
                    log_dir = os.path.expanduser("~/PiFinder_data")
                    for filename in os.listdir(log_dir):
                        if filename.startswith("pifinder") and filename.endswith(
                            ".log"
                        ):
                            file_path = os.path.join(log_dir, filename)
                            zipf.write(file_path, filename)

                # Send the zip file
                def remove_file(response):
                    try:
                        os.remove(zip_path)
                    except Exception:
                        pass
                    return response

                return send_file(
                    zip_path,
                    as_attachment=True,
                    download_name=f"logs_{timestamp}.zip",
                    mimetype="application/zip",
                )

            except Exception as e:
                logger.error(f"Error creating log zip: {e}")
                return app.jinja_env.get_template("logs.html").render(
                    title=_("Logs"), error_message=_("Error creating log archive")
                )

        @app.route("/logs/configs")
        @auth_required
        def list_log_configs():
            """Return all available logconf_*.json files with display names."""
            import glob

            configs = []
            active = (
                os.path.realpath("pifinder_logconf.json")
                if os.path.exists("pifinder_logconf.json")
                else None
            )
            for path in sorted(glob.glob("logconf_*.json")):
                stem = path[len("logconf_") : -len(".json")]
                display = stem.replace("_", " ").title()
                configs.append(
                    {
                        "file": path,
                        "name": display,
                        "active": os.path.realpath(path) == active,
                    }
                )
            return jsonify({"configs": configs})

        @app.route("/logs/switch_config", methods=["POST"])
        @auth_required
        def switch_log_config():
            """Atomically repoint pifinder_logconf.json to the chosen config, then restart."""
            logconf_file = request.form.get("logconf_file", "").strip()
            if (
                not logconf_file
                or not logconf_file.startswith("logconf_")
                or not logconf_file.endswith(".json")
            ):
                return jsonify(
                    {"status": "error", "message": "Invalid log config file name"}
                )
            if not os.path.exists(logconf_file):
                return jsonify(
                    {
                        "status": "error",
                        "message": f"Log config file not found: {logconf_file}",
                    }
                )
            try:
                link = "pifinder_logconf.json"
                tmp = link + ".tmp"
                os.symlink(logconf_file, tmp)
                os.replace(tmp, link)
                logger.info("Switched log config to %s", logconf_file)
            except Exception as e:
                logger.error("Failed to switch log config: %s", e)
                return jsonify({"status": "error", "message": str(e)})
            return app.jinja_env.get_template("restart_pifinder.html").render(
                title=_("Restarting PiFinder")
            )

        @app.route("/logs/upload_config", methods=["POST"])
        @auth_required
        def upload_log_config():
            """Upload a new logconf_*.json file."""
            upload = request.files.get("config_file")
            if not upload:
                logger.warning("No file provided for log config upload")
                return jsonify({"status": "error", "message": "No file provided"})
            filename = upload.filename
            if not filename.startswith("logconf_") or not filename.endswith(".json"):
                logger.warning("Invalid log config file name: %s", filename)
                return jsonify(
                    {
                        "status": "error",
                        "message": "File must be named logconf_<name>.json",
                    }
                )
            if os.path.exists(filename):
                logger.warning("Log config file already exists: %s", filename)
                return jsonify(
                    {
                        "status": "error",
                        "message": f"File already exists: {filename}",
                    }
                )
            try:
                upload.save(filename)
                logger.info("Uploaded log config: %s", filename)
                return jsonify({"status": "ok", "file": filename})
            except Exception as e:
                logger.error("Failed to save uploaded log config: %s", e)
                return jsonify({"status": "error", "message": str(e)})

        @app.route("/tools/backup")
        @auth_required
        def tools_backup():
            _backup_file = sys_utils.backup_userdata()

            # Assumes the standard backup location
            return send_file(
                os.path.expanduser("~/PiFinder_data/PiFinder_backup.zip"),
                as_attachment=True,
            )

        @app.route("/tools/restore", methods=["POST"])
        @auth_required
        def tools_restore():
            sys_utils.remove_backup()
            backup_file = request.files.get("backup_file")
            if backup_file:
                backup_file.save(
                    os.path.expanduser("~/PiFinder_data/PiFinder_backup.zip")
                )

                sys_utils.restore_userdata(
                    os.path.expanduser("~/PiFinder_data/PiFinder_backup.zip")
                )

            return app.jinja_env.get_template("restart_pifinder.html").render(
                title=_("Restart PiFinder")
            )

        @app.route("/key_callback", methods=["POST"])
        @auth_required
        def key_callback():
            button = request.json.get("button")
            if button in self.button_dict:
                self.key_callback(self.button_dict[button])
            else:
                self.key_callback(int(button))
            return jsonify({"message": "success"})

        @app.route("/api/current-selection")
        @auth_required
        def current_selection():
            """
            Returns information about the currently active UI item for testing purposes
            """
            try:
                ui_state_data = self.shared_state.current_ui_state()
                if ui_state_data is None:
                    return jsonify({"error": "UI state not available"})

                return jsonify(ui_state_data)

            except Exception as e:
                logger.error(f"Error getting current UI state: {e}")
                return jsonify({"error": str(e)})

        @app.route("/image")
        def serve_pil_image():
            empty_img = Image.new(
                "RGB", (60, 30), color=(73, 109, 137)
            )  # create an image using PIL
            img = None
            try:
                img = self.shared_state.screen()
            except (BrokenPipeError, EOFError):
                pass

            if img is None:
                img = empty_img
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format="PNG")  # adjust for your image format
            img_byte_arr.seek(0)

            return send_file(img_byte_arr, mimetype="image/png")

        # # If you want to see a log of all requests for debugging, you can uncomment this:
        # @app.after_request
        # def log_request(response):
        #     logger.debug(
        #         "%s %s %s", request.method, request.path, response.status_code
        #     )
        #     return response

        try:
            from PiFinder.api_extensions import register_api_routes

            register_api_routes(app, self, require_auth=False)
        except Exception:
            logger.exception("Failed to register API extension routes")

        @auth_required
        def gps_lock(lat: float = 50, lon: float = 3, altitude: float = 10):
            msg = (
                "fix",
                {
                    "lat": lat,
                    "lon": lon,
                    "altitude": altitude,
                    "error_in_m": 0,
                    "source": "WEB",
                    "lock": True,
                },
            )
            self.gps_queue.put(msg)
            logger.debug("Putting location msg on gps_queue: {msg}")

        def time_lock(time=timez.local_now()):
            msg = ("time", time)
            self.gps_queue.put(msg)
            logger.debug("Putting time msg on gps_queue: {msg}")

        # Store the app reference for running
        self.app = app

    def run(self):
        # If the PiFinder software is running as a service
        # it can grab port 80.  If not, it needs to use 8080
        try:
            waitress_serve(self.app, host="0.0.0.0", port=80)
            logger.info("Webserver started on port 80")
        except (PermissionError, OSError) as e:
            logger.debug(f"Permission denied on port 80, trying 8080. {e}")
            try:
                waitress_serve(self.app, host="0.0.0.0", port=8080)
                logger.info("Webserver started on port 8080")
            except Exception as e2:
                logger.exception(f"Failed to start server on port 8080. {e2}")
                raise
        logger.debug("Webserver is running")

    def key_callback(self, key):
        self.keyboard_queue.put(key)

    def update_gps(self):
        """Update GPS information"""
        location = self.shared_state.location()

        if location.lock is True:
            self.gps_locked = True
            self.lat = location.lat
            self.lon = location.lon
            self.altitude = location.altitude
        else:
            self.gps_locked = False
            self.lat = None
            self.lon = None
            self.altitude = None


def run_server(
    keyboard_queue,
    ui_queue,
    gps_queue,
    shared_state,
    log_queue,
    verbose=False,
    mountcontrol_queue=None,
):
    MultiprocLogging.configurer(log_queue)
    server = Server(
        keyboard_queue,
        ui_queue,
        gps_queue,
        mountcontrol_queue,
        shared_state,
        verbose,
    )
    server.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PiFinder Flask Web Server with i18n support"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to run server on (default: 8080)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )

    args = parser.parse_args()

    # Setup basic logging for standalone mode
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s:%(levelname)s:%(message)s",
    )

    logger.info("Starting PiFinder Server in standalone mode")

    # Create a single queue for command line testing
    test_queue: multiprocessing.Queue = multiprocessing.Queue()

    # Create server with mock components
    server = Server(
        keyboard_queue=test_queue,
        ui_queue=test_queue,
        gps_queue=test_queue,
        shared_state=MockSharedState(),
        is_debug=args.debug,
    )

    # Override the default port behavior for command line usage
    try:
        logger.info("Starting web server.")
        server.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)
