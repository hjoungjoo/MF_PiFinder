"""Offline coverage for the passive Advanced > Lens declaration UI."""

import pytest

import PiFinder.i18n  # noqa: F401
from PiFinder.ui import callbacks, menu_structure

pytestmark = pytest.mark.unit


class _Config:
    def __init__(self, lens=""):
        self.lens = lens

    def get_option(self, option, default=None):
        assert option == "camera_lens"
        return self.lens if self.lens is not None else default


class _State:
    def __init__(self):
        self.lens = None

    def set_camera_lens(self, lens):
        self.lens = lens


class _UI:
    def __init__(self, lens=""):
        self.config_object = _Config(lens)
        self.shared_state = _State()


def _lens_menu():
    def walk(node):
        if isinstance(node, dict):
            if node.get("name") == "Lens":
                return node
            for child in node.get("items", []):
                result = walk(child)
                if result:
                    return result
        return None

    result = walk(menu_structure.pifinder_menu)
    assert result is not None
    return result


def test_lens_menu_is_a_single_config_declaration():
    menu = _lens_menu()
    assert menu["config_option"] == "camera_lens"
    assert menu["post_callback"] is callbacks.set_camera_lens
    assert [item["value"] for item in menu["items"]] == [
        "",
        "4mm",
        "6mm",
        "8mm",
        "10mm",
        "12mm",
        "16mm",
        "25mm",
    ]


def test_set_camera_lens_publishes_valid_lens_without_restart():
    ui = _UI("12mm")
    callbacks.set_camera_lens(ui)
    assert ui.shared_state.lens == "12mm"


def test_set_camera_lens_rejects_unrecognised_config_value():
    ui = _UI("bad-lens")
    callbacks.set_camera_lens(ui)
    assert ui.shared_state.lens == ""
