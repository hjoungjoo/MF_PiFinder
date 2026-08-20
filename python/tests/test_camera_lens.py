"""Offline coverage for the passive Advanced > Lens declaration UI."""

import pytest

import PiFinder.i18n  # noqa: F401
from PiFinder.ui import callbacks, menu_structure

pytestmark = pytest.mark.unit


class _Config:
    def __init__(self, lens="", focal_length=None):
        self.lens = lens
        self.focal_length = focal_length
        self.saved = {}

    def get_option(self, option, default=None):
        if option == "camera_lens":
            return self.lens if self.lens is not None else default
        if option == "camera_lens_focal_length_mm":
            return self.focal_length if self.focal_length is not None else default
        raise AssertionError(option)

    def set_option(self, option, value):
        self.saved[option] = value


class _State:
    def __init__(self):
        self.lens = None

    def set_camera_lens(self, lens):
        self.lens = lens

    def set_camera_lens_focal_length_mm(self, focal_length):
        self.focal_length = focal_length


class _UI:
    def __init__(self, lens=""):
        self.config_object = _Config(lens)
        self.shared_state = _State()
        self.pushed = None
        self.messages = []

    def add_to_stack(self, item):
        self.pushed = item

    def message(self, message, timeout):
        self.messages.append((message, timeout))


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


def test_manual_lens_menu_opens_one_decimal_entry_and_publishes_value():
    ui = _UI()
    callbacks.edit_manual_lens_focal_length(ui)
    assert ui.pushed["max_length"] == 4
    assert ui.pushed["initial_text"] == ""

    ui.pushed["callback"]("7.64")
    assert ui.config_object.saved["camera_lens_focal_length_mm"] == 7.6
    assert ui.shared_state.focal_length == 7.6
