from types import SimpleNamespace

from PiFinder.ui.base import GuideKeyMixin, UIModule
from PiFinder.ui.preview import UIPreview


class DummyQueue:
    def __init__(self):
        self.commands = []

    def put(self, command):
        self.commands.append(command)


class DummyGuideScreen(GuideKeyMixin, UIModule):
    pass


def _screen(mount_control=True):
    screen = DummyGuideScreen.__new__(DummyGuideScreen)
    screen.config_object = SimpleNamespace(
        get_option=lambda name, default=None: mount_control
        if name == "mount_control"
        else default
    )
    screen.command_queues = {"mountcontrol": DummyQueue()}
    return screen


def test_guide_mixin_number_key_runs_unified_mount_command():
    # Number keys now run the unified discrete mount command map (single-step
    # nudge on 2/4/6/8), not an 8-way hold-to-move jog. Release is a no-op.
    screen = _screen()

    screen.key_number_press(8)
    screen.key_number_release(8)

    assert screen.command_queues["mountcontrol"].commands == [
        {"type": "manual_movement", "direction": "north"},
    ]


def test_guide_mixin_number_step_size_keys():
    screen = _screen()

    screen.key_number(9)
    screen.key_number(3)

    assert screen.command_queues["mountcontrol"].commands == [
        {"type": "increase_step_size"},
        {"type": "reduce_step_size"},
    ]


def test_guide_mixin_text_keys_match_guide_layout():
    screen = _screen()

    screen.key_text_press("q")
    screen.key_text_release("q")
    screen.key_text("e")
    screen.key_text("s")

    assert screen.command_queues["mountcontrol"].commands == [
        {
            "type": "manual_movement",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
        {"type": "stop_movement"},
        {
            "type": "manual_movement",
            "direction": "northeast",
            "lease_seconds": 2.5,
        },
        {"type": "stop_movement"},
    ]


def test_guide_mixin_press_keeps_motion_alive_until_release():
    screen = _screen()

    screen.key_text_press("q")
    screen._guide_next_motion_keepalive_at = 0.0
    screen._guide_send_motion_keepalive()
    screen.key_text_release("q")
    screen._guide_next_motion_keepalive_at = 0.0
    screen._guide_send_motion_keepalive()

    assert screen.command_queues["mountcontrol"].commands == [
        {
            "type": "manual_movement",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
        {
            "type": "manual_movement_keepalive",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
        {"type": "stop_movement"},
    ]


def test_guide_mixin_long_hold_restarts_motion_before_controller_limit():
    screen = _screen()

    screen.key_text_press("q")
    screen._guide_next_motion_restart_at = -1.0
    screen._guide_send_motion_keepalive()

    assert screen.command_queues["mountcontrol"].commands == [
        {
            "type": "manual_movement",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
        {
            "type": "manual_movement",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
    ]


def test_guide_mixin_plain_text_event_does_not_keep_motion_alive():
    screen = _screen()

    screen.key_text("q")
    screen._guide_next_motion_keepalive_at = 0.0
    screen._guide_send_motion_keepalive()

    assert screen.command_queues["mountcontrol"].commands == [
        {
            "type": "manual_movement",
            "direction": "northwest",
            "lease_seconds": 2.5,
        },
    ]


def test_guide_mixin_plus_minus_adjust_slew_rate():
    screen = _screen()

    screen.key_plus()
    screen.key_minus()

    assert screen.command_queues["mountcontrol"].commands == [
        {"type": "increase_slew_rate"},
        {"type": "reduce_slew_rate"},
    ]


def test_guide_mixin_is_noop_when_mount_control_is_off():
    screen = _screen(mount_control=False)

    screen.key_number_press(8)
    screen.key_text_press("q")
    screen.key_plus()

    assert screen.command_queues["mountcontrol"].commands == []


def test_focus_preview_uses_guide_keys_without_overriding_zoom_controls():
    assert issubclass(UIPreview, GuideKeyMixin)
    assert UIPreview.key_plus is not GuideKeyMixin.key_plus
    assert UIPreview.key_minus is not GuideKeyMixin.key_minus
