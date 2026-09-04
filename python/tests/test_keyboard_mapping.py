"""Configurable physical-keyboard mount shortcut behavior."""

import queue

import pytest

from PiFinder import keyboard_mapping
from PiFinder.keyboard_interface import KeyboardInterface


def _drain(target: queue.Queue) -> list:
    items = []
    while not target.empty():
        items.append(target.get_nowait())
    return items


def _dispatcher(mount_enabled=True):
    keyboard = queue.Queue()
    mount = queue.Queue()
    dispatcher = keyboard_mapping.KeyboardDispatcher(
        keyboard, mount, lambda: mount_enabled
    )
    return dispatcher, keyboard, mount


@pytest.mark.unit
def test_actions_are_exactly_the_requested_mount_functions():
    assert [action for action, _label in keyboard_mapping.ACTIONS] == [
        "mount_up",
        "mount_down",
        "mount_left",
        "mount_right",
        "speed_up",
        "speed_down",
        "goto_start",
        "tracking_off",
    ]


@pytest.mark.unit
def test_direction_key_holds_with_keepalive_and_stops_on_release():
    dispatcher, _, mount = _dispatcher()
    dispatcher.set_mapping({"mount_up": "KEY_103"})

    assert dispatcher.handle_key("KEY_103", True, 10.0)
    dispatcher.tick(10.0 + keyboard_mapping.MANUAL_MOTION_KEEPALIVE_INTERVAL + 0.01)
    assert dispatcher.handle_key("KEY_103", False, 11.0)

    assert _drain(mount) == [
        {
            "type": "manual_movement",
            "direction": "north",
            "lease_seconds": keyboard_mapping.MANUAL_MOTION_LEASE_SECONDS,
        },
        {
            "type": "manual_movement_keepalive",
            "direction": "north",
            "lease_seconds": keyboard_mapping.MANUAL_MOTION_LEASE_SECONDS,
        },
        {"type": "stop_movement"},
    ]


@pytest.mark.unit
def test_speed_tracking_and_goto_dispatch():
    dispatcher, keyboard, mount = _dispatcher()
    dispatcher.set_mapping(
        {
            "speed_up": "KEY_78",
            "speed_down": "KEY_74",
            "goto_start": "KEY_28",
            "tracking_off": "KEY_20",
        }
    )

    for identifier in ("KEY_78", "KEY_74", "KEY_28", "KEY_20"):
        dispatcher.handle_key(identifier, True, 0.0)
        dispatcher.handle_key(identifier, False, 0.1)

    assert _drain(keyboard) == [
        KeyboardInterface.number_press_key(5),
        KeyboardInterface.number_release_key(5),
    ]
    assert _drain(mount) == [
        {"type": "increase_slew_rate", "notify_ui": True},
        {"type": "reduce_slew_rate", "notify_ui": True},
        {"type": "set_tracking", "enabled": False},
    ]


class _Config:
    def __init__(self, mapping=None, mount_control=True):
        self.mapping = mapping or {}
        self.mount_control = mount_control

    def get_option(self, name, default=None):
        if name == keyboard_mapping.CONFIG_KEY:
            return self.mapping
        if name == "mount_control":
            return self.mount_control
        return default


@pytest.mark.unit
def test_unmapped_event_passes_normal_pifinder_keycode_through():
    keyboard = queue.Queue()
    manager = keyboard_mapping.KeyboardMappingManager()
    manager.start(keyboard, queue.Queue(), _Config())

    event = keyboard_mapping.make_event(30, True, KeyboardInterface.text_press_key("a"))
    assert manager.handle_event(event, 1.0) == KeyboardInterface.text_press_key("a")


@pytest.mark.unit
def test_capture_records_key_and_suppresses_press_and_release():
    manager = keyboard_mapping.KeyboardMappingManager()
    manager.start(queue.Queue(), queue.Queue(), _Config())
    manager.start_capture()

    assert manager.handle_event(keyboard_mapping.make_event(30, True, 123), 1.0) is None
    assert manager.take_captured() == "KEY_30"
    assert manager.last_key_code == 30
    assert (
        manager.handle_event(keyboard_mapping.make_event(30, False, 456), 1.1) is None
    )


@pytest.mark.unit
def test_repeat_is_consumed_but_release_still_stops_mapped_motion():
    keyboard = queue.Queue()
    mount = queue.Queue()
    manager = keyboard_mapping.KeyboardMappingManager()
    manager.start(keyboard, mount, _Config({"mount_left": "KEY_30"}))

    assert manager.handle_event(keyboard_mapping.make_event(30, True), 1.0) is None
    assert (
        manager.handle_event(
            keyboard_mapping.make_event(30, True, 999, repeat=True), 2.0
        )
        is None
    )
    assert manager.handle_event(keyboard_mapping.make_event(30, False), 2.1) is None

    assert _drain(mount)[-1] == {"type": "stop_movement"}
    assert _drain(keyboard) == []


@pytest.mark.unit
def test_clearing_mapping_stops_a_held_direction():
    dispatcher, _, mount = _dispatcher()
    dispatcher.set_mapping({"mount_down": "KEY_108"})
    dispatcher.handle_key("KEY_108", True, 1.0)

    dispatcher.set_mapping({})
    dispatcher.tick(10.0)

    assert _drain(mount)[-1] == {"type": "stop_movement"}


@pytest.mark.unit
def test_key_labels_are_human_readable_with_unknown_fallback():
    assert keyboard_mapping.key_label("KEY_103") == "Up"
    assert keyboard_mapping.key_label("KEY_999") == "Key 999"
