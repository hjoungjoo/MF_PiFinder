import inspect
from pathlib import Path

import pytest

from PiFinder import camera_controls


def test_auto_modes_pass_through():
    assert camera_controls.normalize_exposure("auto") == ("auto", None)
    assert camera_controls.normalize_exposure(" AUTO_STAR ") == ("auto_star", None)


def test_manual_exposure_is_coerced_to_int_microseconds():
    value, note = camera_controls.normalize_exposure("400000")
    assert value == 400000
    assert note is None
    # camera_interface parses manual exposures with int(), so a float request
    # must not reach the queue as "400000.0".
    assert camera_controls.exposure_command(400000.0) == "set_exp:400000"


def test_exposure_out_of_range_is_clamped_with_a_note():
    value, note = camera_controls.normalize_exposure(5_000_000)
    assert value == camera_controls.MAX_EXPOSURE_US
    assert note is not None

    value, note = camera_controls.normalize_exposure(10)
    assert value == camera_controls.MIN_EXPOSURE_US
    assert note is not None


def test_invalid_exposure_raises():
    for bad in ["", "fast", None, True, {}]:
        with pytest.raises(ValueError):
            camera_controls.normalize_exposure(bad)


def test_gain_profile_and_numeric():
    assert camera_controls.normalize_gain("profile") == ("profile", None)
    assert camera_controls.normalize_gain("20") == (20.0, None)
    assert camera_controls.gain_command("profile") == "set_gain:profile"
    assert camera_controls.gain_command(20.0) == "set_gain:20"
    assert camera_controls.gain_command(2.5) == "set_gain:2.5"


def test_gain_out_of_range_is_clamped_with_a_note():
    value, note = camera_controls.normalize_gain(100)
    assert value == camera_controls.MAX_GAIN
    assert note is not None


def test_invalid_gain_raises():
    for bad in ["", "high", None, False]:
        with pytest.raises(ValueError):
            camera_controls.normalize_gain(bad)


def test_exposure_mode_reporting():
    assert camera_controls.exposure_mode("auto") == "auto"
    assert camera_controls.exposure_mode("auto_star") == "auto_star"
    assert camera_controls.exposure_mode(400000) == "manual"
    assert camera_controls.exposure_mode(None) == "manual"


def test_web_server_is_wired_to_the_camera_command_queue():
    # Without this queue the web camera controls have nowhere to send
    # set_exp:/set_gain:, and the API reports itself unavailable.
    from PiFinder import server

    assert "camera_command_queue" in inspect.signature(server.run_server).parameters
    assert (
        "camera_command_queue" in inspect.signature(server.Server.__init__).parameters
    )

    main_source = (Path(__file__).parents[1] / "PiFinder" / "main.py").read_text()
    webserver_process = main_source[main_source.index('name="Webserver"') :]
    webserver_process = webserver_process[: webserver_process.index(".start()")]
    assert "camera_command_queue" in webserver_process


def test_presets_match_the_on_device_menu():
    # The web page renders its dropdowns from these, and they are meant to be
    # the same choices ui/menu_structure.py offers on the device.
    import PiFinder.i18n  # noqa: F401  (installs the gettext _ builtin)
    from PiFinder.ui import menu_structure

    def _menu_values(label):
        stack = [menu_structure.pifinder_menu]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("label") == label:
                    return [item["value"] for item in node["items"]]
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        raise AssertionError(f"menu {label} not found")

    exposure_values = _menu_values("camera_exposure")
    assert [v for v in exposure_values if isinstance(v, int)] == list(
        camera_controls.EXPOSURE_PRESETS_US
    )
    assert [v for v in exposure_values if isinstance(v, str)] == list(
        camera_controls.AUTO_EXPOSURE_MODES
    )

    gain_values = _menu_values("camera_gain")
    assert [v for v in gain_values if isinstance(v, (int, float))] == list(
        camera_controls.GAIN_PRESETS
    )
