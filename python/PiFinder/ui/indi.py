#!/usr/bin/python
# -*- coding:utf-8 -*-

import json
import time

from PIL import ImageChops

from PiFinder import utils
from PiFinder.indi_align import BRIGHT_ALIGN_STARS, clamp_align_points
from PiFinder.ui.base import UIModule
from PiFinder.ui.camera_render import resize_for_display


STATUS_FILE = utils.data_dir / "mount_control_status.json"

SLEW_STEPS = [
    "Off",
    "1/2x",
    "1x",
    "2x",
    "4x",
    "8x",
    "20x",
    "48x",
    "1/2 Max",
    "Max",
]
MANUAL_MOTION_KEEPALIVE_INTERVAL = 0.4
MANUAL_MOTION_LEASE_SECONDS = 1.2


class UIIndiBase(UIModule):
    def _mount_queue(self):
        if not self.config_object.get_option("mount_control", False):
            return None
        return self.command_queues.get("mountcontrol")

    def _send_mount(self, command):
        mount_queue = self._mount_queue()
        if mount_queue is None:
            self.message(_("Mount Control Off"), 1)
            return False
        mount_queue.put(command)
        return True

    def _status(self):
        try:
            with open(STATUS_FILE, encoding="utf-8") as status_in:
                return json.load(status_in)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"state": "unknown", "message": _("No status")}

    def _current_pointing_radec(self):
        solution = self.shared_state.solution()
        if not solution or not solution.has_pointing():
            return None
        aligned = solution.pointing.aligned.estimate
        if aligned is None:
            return None
        return aligned.RA, aligned.Dec

    def _draw_text(self, xy, text, font=None, fill=None):
        self.draw.text(
            xy,
            text,
            font=font or self.fonts.bold.font,
            fill=fill or self.colors.get(255),
        )


class UIIndiInit(UIIndiBase):
    __title__ = "INDI INIT"

    def update(self, force=False):
        self.clear_screen()
        status = self._status()
        y = self.display_class.titlebar_height + 6
        line_h = max(12, self.fonts.bold.height + 2)

        state = status.get("state", "unknown")
        message = status.get("message", "")
        step = status.get("step_degrees")
        slew_rate = status.get("slew_rate")
        ra = status.get("ra")
        dec = status.get("dec")

        self._draw_text((6, y), _("State: {state}").format(state=state))
        y += line_h
        if message:
            self._draw_text((6, y), str(message)[:24], fill=self.colors.get(160))
            y += line_h
        if step is not None:
            self._draw_text((6, y), _("Step: {step:.2f} deg").format(step=float(step)))
            y += line_h
        if slew_rate is not None:
            rate = int(slew_rate)
            label = SLEW_STEPS[rate] if 0 <= rate < len(SLEW_STEPS) else ""
            self._draw_text(
                (6, y), _("Speed: {rate} {label}").format(rate=rate, label=label)
            )
            y += line_h
        if ra is not None and dec is not None:
            self._draw_text(
                (6, y),
                _("RA {ra:.1f} Dec {dec:.1f}").format(ra=float(ra), dec=float(dec)),
                fill=self.colors.get(160),
            )
            y += line_h

        location = self.shared_state.location()
        if location and getattr(location, "lock", False):
            loc_text = _("Loc: {lat:.4f},{lon:.4f}").format(
                lat=float(location.lat), lon=float(location.lon)
            )
        else:
            loc_text = _("Loc: default/GPS none")
        self._draw_text((6, y), loc_text[:28])
        y += line_h

        hint_font = self.fonts.small.font
        hint_line_h = max(10, self.fonts.small.height + 2)
        hints = [
            _("1 Init   2 Time"),
            _("3 Park   6 Unpark"),
            _("4 Home   5 Return"),
            _("7 SetPark"),
            _("8 Restart"),
        ]
        hint_y = max(y + 4, self.display_class.resY - (len(hints) * hint_line_h) - 2)
        for hint in hints:
            self._draw_text(
                (6, hint_y), hint, font=hint_font, fill=self.colors.get(128)
            )
            hint_y += hint_line_h

        return self.screen_update()

    def key_number(self, number):
        if number == 1:
            if self._send_mount({"type": "init"}):
                self.message(_("INDI Init"), 1)
        elif number == 2:
            if self._send_mount({"type": "sync_location_time"}):
                self.message(_("Time/Location"), 1)
        elif number == 3:
            if self._send_mount({"type": "park_action", "action": "park"}):
                self.message(_("Park"), 1)
        elif number == 4:
            if self._send_mount({"type": "park_action", "action": "set_home"}):
                self.message(_("Set Home"), 1)
        elif number == 5:
            if self._send_mount({"type": "park_action", "action": "return_home"}):
                self.message(_("Return Home"), 1)
        elif number == 6:
            if self._send_mount({"type": "park_action", "action": "unpark"}):
                self.message(_("Unpark"), 1)
        elif number == 7:
            if self._send_mount({"type": "park_action", "action": "set_park"}):
                self.message(_("Set-Park"), 1)
        elif number == 8:
            if self._send_mount({"type": "restart_driver"}):
                self.message(_("INDI Restart"), 1)

    def key_square(self):
        self.key_number(1)


class UIIndiStatus(UIIndiBase):
    __title__ = "INDI STATUS"

    def _format_age(self, updated):
        if not updated:
            return "--"
        age = max(0.0, time.time() - float(updated))
        if age < 60:
            return f"{age:.0f}s ago"
        return f"{age / 60:.1f}m ago"

    def _format_float(self, value, places=2):
        if value is None:
            return "--"
        return f"{float(value):.{places}f}"

    def update(self, force=False):
        self.clear_screen()
        status = self._status()
        y = self.display_class.titlebar_height + 4
        line_h = max(10, self.fonts.small.height + 2)
        font = self.fonts.small.font

        rows = [
            ("State", status.get("state", "unknown")),
            ("Msg", status.get("message", "--")),
            ("Age", self._format_age(status.get("updated"))),
            ("Device", status.get("device", "--")),
            ("Home", status.get("home_state", "--")),
            ("Park", status.get("park_state", "--")),
            ("Raw", status.get("raw_mount_status", "--")),
            ("RA", self._format_float(status.get("ra"), 2)),
            ("Dec", self._format_float(status.get("dec"), 2)),
            ("Speed", status.get("slew_rate", "--")),
            ("Step", self._format_float(status.get("step_degrees"), 2)),
        ]
        if status.get("target_ra") is not None or status.get("target_dec") is not None:
            rows.extend(
                [
                    ("Tgt RA", self._format_float(status.get("target_ra"), 2)),
                    ("Tgt Dec", self._format_float(status.get("target_dec"), 2)),
                ]
            )

        key_w = 7
        max_chars = max(8, self.display_class.resX // self.fonts.small.width)
        for key, value in rows:
            if y + line_h > self.display_class.resY:
                break
            text = f"{key:<{key_w}} {value}"
            self.draw.text(
                (4, y),
                str(text)[:max_chars],
                font=font,
                fill=self.colors.get(192),
            )
            y += line_h

        return self.screen_update()


class UIIndiBacklash(UIIndiBase):
    __title__ = "INDI BACKLASH"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_axis = "ra"
        self.inputs = {"ra": "", "de": ""}

    def active(self):
        self._send_mount({"type": "refresh_backlash"})

    def _status_value(self, axis):
        status = self._status()
        value = status.get(f"backlash_{axis}")
        if value is None:
            return "--"
        return str(value)

    def _input_value(self, axis):
        if self.inputs[axis] != "":
            return self.inputs[axis]
        current = self._status_value(axis)
        return "" if current == "--" else current

    def _draw_row(self, y, axis, label):
        selected = axis == self.selected_axis
        marker = ">" if selected else " "
        value = self._input_value(axis) or "--"
        current = self._status_value(axis)
        fill = self.colors.get(255 if selected else 160)
        self._draw_text(
            (4, y),
            f"{marker}{label} in:{value:<3} cur:{current:<3}"[:28],
            font=self.fonts.small.font,
            fill=fill,
        )

    def update(self, force=False):
        self.clear_screen()
        status = self._status()
        y = self.display_class.titlebar_height + 4
        line_h = max(10, self.fonts.small.height + 2)

        self._draw_row(y, "ra", "RA")
        y += line_h
        self._draw_row(y, "de", "DE")
        y += line_h

        auto = status.get("backlash_auto", {})
        auto_message = auto.get("message") or status.get("message") or _("Idle")
        self._draw_text(
            (4, y),
            str(auto_message)[:28],
            font=self.fonts.small.font,
            fill=self.colors.get(128),
        )
        y += line_h
        if auto.get("axis"):
            self._draw_text(
                (4, y),
                _("Axis: {axis}").format(axis=auto.get("axis")),
                font=self.fonts.small.font,
                fill=self.colors.get(128),
            )

        hints = [
            _("+/- Axis"),
            _("Digits input  0 clear"),
            _("Right Auto  Square Save"),
        ]
        hint_y = self.display_class.resY - (len(hints) * line_h) - 2
        for hint in hints:
            self._draw_text(
                (4, hint_y),
                hint[:28],
                font=self.fonts.small.font,
                fill=self.colors.get(128),
            )
            hint_y += line_h

        return self.screen_update()

    def _toggle_axis(self):
        self.selected_axis = "de" if self.selected_axis == "ra" else "ra"
        self.update()

    def key_plus(self):
        self._toggle_axis()

    def key_minus(self):
        self._toggle_axis()

    def key_number(self, number):
        axis = self.selected_axis
        if number == 0:
            self.inputs[axis] = ""
        elif len(self.inputs[axis]) < 3:
            candidate = f"{self.inputs[axis]}{number}"
            if 0 <= int(candidate) <= 999:
                self.inputs[axis] = candidate
        self.update()

    def key_right(self):
        if self._send_mount({"type": "auto_backlash", "axis": self.selected_axis}):
            self.message(_("Auto Backlash"), 1)
        return False

    def key_square(self):
        ra_value = self._input_value("ra") or "0"
        de_value = self._input_value("de") or "0"
        if self._send_mount(
            {
                "type": "set_backlash",
                "ra": ra_value,
                "de": de_value,
            }
        ):
            self.message(_("Backlash Saved"), 1)


class UIIndiGuide(UIIndiBase):
    __title__ = "INDI GUIDE"

    _number_direction = {
        1: "southwest",
        2: "south",
        3: "southeast",
        4: "west",
        6: "east",
        7: "northwest",
        8: "north",
        9: "northeast",
    }
    _text_direction = {
        "z": "southwest",
        "x": "south",
        "c": "southeast",
        "a": "west",
        "s": "stop",
        "d": "east",
        "q": "northwest",
        "w": "north",
        "e": "northeast",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._active_motion_direction = None
        self._next_motion_keepalive_at = 0.0

    def active(self):
        self._active_motion_direction = None
        self._next_motion_keepalive_at = 0.0
        self._send_mount({"type": "refresh_slew_rate"})

    def inactive(self):
        if self._active_motion_direction is not None:
            self._send_mount({"type": "stop_movement"})
        self._active_motion_direction = None
        self._next_motion_keepalive_at = 0.0

    def _send_motion_keepalive(self):
        if self._active_motion_direction is None:
            return

        now = time.monotonic()
        if now < self._next_motion_keepalive_at:
            return

        self._send_mount(
            {
                "type": "manual_movement_keepalive",
                "direction": self._active_motion_direction,
                "lease_seconds": MANUAL_MOTION_LEASE_SECONDS,
            }
        )
        self._next_motion_keepalive_at = now + MANUAL_MOTION_KEEPALIVE_INTERVAL

    def _draw_camera_background(self):
        try:
            image_obj = self.camera_image.copy()
            image_obj = resize_for_display(
                image_obj,
                (self.display_class.resX, self.display_class.resY),
                0,
            )
            image_obj = image_obj.convert("L").convert("RGB")
            image_obj = ImageChops.multiply(image_obj, self.colors.red_image)
            self.screen.paste(image_obj)
        except Exception:
            self.clear_screen()

    def _draw_keypad_overlay(self):
        status = self._status()
        slew_rate = int(status.get("slew_rate", 5))
        label = SLEW_STEPS[slew_rate] if 0 <= slew_rate < len(SLEW_STEPS) else ""
        font = self.fonts.base.font
        line_h = self.fonts.base.height + 2
        bright = self.colors.get(192)
        shadow = self.colors.get(0)

        def text_size(text):
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]

        def overlay_text(x, y, text, fill=bright):
            self.draw.text((x + 1, y + 1), text, font=font, fill=shadow)
            self.draw.text((x, y), text, font=font, fill=fill)

        def centered(y, text):
            width, _height = text_size(text)
            overlay_text((self.display_class.resX - width) // 2, y, text)

        top_y = self.display_class.titlebar_height + 2
        center_y = (self.display_class.resY - line_h) // 2
        bottom_hint_y = self.display_class.resY - (line_h * 5) - 2
        bottom_key_y = max(center_y + line_h + 2, bottom_hint_y - line_h - 2)
        side_key_y = (top_y + bottom_key_y) // 2

        key_9_w, _height = text_size("9")
        key_3_w, _height = text_size("3")
        overlay_text(4, top_y, "7")
        centered(top_y, "8")
        overlay_text(self.display_class.resX - key_9_w - 4, top_y, "9")
        overlay_text(4, side_key_y, "4")
        right_w, _height = text_size("6")
        overlay_text(self.display_class.resX - right_w - 4, side_key_y, "6")
        overlay_text(4, bottom_key_y, "1")
        centered(bottom_key_y, "2")
        overlay_text(self.display_class.resX - key_3_w - 4, bottom_key_y, "3")

        overlay_text(4, bottom_hint_y, _("Release: stop"))
        overlay_text(
            4,
            bottom_hint_y + line_h,
            _("+/-:Speed {rate} {label}").format(rate=slew_rate, label=label),
        )
        refine = self.config_object.get_option("indi_goto_refine_once", False)
        overlay_text(
            4,
            bottom_hint_y + (line_h * 2),
            _("5:Refine {state}").format(state=_("On") if refine else _("Off")),
        )
        guide_corr = status.get("guide_correction_enabled", False)
        overlay_text(
            4,
            bottom_hint_y + (line_h * 3),
            _("0:Guide {state}").format(state=_("On") if guide_corr else _("Off")),
        )
        overlay_text(4, bottom_hint_y + (line_h * 4), _("Square:align"))

    def update(self, force=False):
        self._send_motion_keepalive()
        self._draw_camera_background()
        self._draw_keypad_overlay()
        return self.screen_update(title_bar=True, button_hints=False)

    def _move(self, direction):
        if direction == "stop":
            self._active_motion_direction = None
            self._next_motion_keepalive_at = 0.0
            self._send_mount({"type": "stop_movement"})
        else:
            self._active_motion_direction = direction
            self._next_motion_keepalive_at = (
                time.monotonic() + MANUAL_MOTION_KEEPALIVE_INTERVAL
            )
            self._send_mount(
                {
                    "type": "manual_movement",
                    "direction": direction,
                    "lease_seconds": MANUAL_MOTION_LEASE_SECONDS,
                }
            )
        self.update(force=True)

    def key_number(self, number):
        if number == 0:
            self._send_mount(
                {
                    "type": "toggle_guide_correction",
                    "accuracy_arcmin": self.config_object.get_option(
                        "indi_goto_refine_accuracy_arcmin", 10.0
                    ),
                }
            )
            self.message(_("Guide Correction"), 1)
        elif number == 5:
            enabled = not self.config_object.get_option("indi_goto_refine_once", False)
            self.config_object.set_option("indi_goto_refine_once", enabled)
            self.command_queues["ui_queue"].put("reload_config")
            self.message(_("Refine On") if enabled else _("Refine Off"), 1)

    def key_number_press(self, number):
        if number in (0, 5):
            self.key_number(number)
            return
        direction = self._number_direction.get(number)
        if direction:
            self._move(direction)

    def key_number_release(self, number):
        if number in (0, 5):
            return
        if number in self._number_direction:
            self._move("stop")

    def key_text(self, char: str):
        # Legacy text events have no release pair, so only allow explicit stop.
        direction = self._text_direction.get(char.lower())
        if direction == "stop":
            self._move(direction)

    def key_text_press(self, char: str):
        direction = self._text_direction.get(char.lower())
        if direction:
            self._move(direction)

    def key_text_release(self, char: str):
        if char.lower() in self._text_direction:
            self._move("stop")

    def key_plus(self):
        self._send_mount({"type": "increase_slew_rate"})
        self.message(_("Speed +"), 0.5)

    def key_minus(self):
        self._send_mount({"type": "reduce_slew_rate"})
        self.message(_("Speed -"), 0.5)

    def key_square(self):
        pointing = self._current_pointing_radec()
        if pointing is None:
            self.message(_("No solve"), 1)
            return
        if self._send_mount({"type": "sync", "ra": pointing[0], "dec": pointing[1]}):
            self.message(_("Aligned"), 1)


class UIIndiMultiPointAlign(UIIndiGuide):
    __title__ = "INDI ALIGN"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.align_points = 3
        self.star_index = 0

    def _align_status(self):
        status = self._status()
        align_status = status.get("multipoint_align", {})
        return align_status if isinstance(align_status, dict) else {}

    def _selected_star(self):
        return BRIGHT_ALIGN_STARS[self.star_index % len(BRIGHT_ALIGN_STARS)]

    def _current_star(self):
        return self._align_status().get("current_star") or {}

    def _is_adjusting(self):
        align_status = self._align_status()
        return bool(align_status.get("active") and align_status.get("current_star"))

    def _draw_setup(self):
        self.clear_screen()
        align_status = self._align_status()
        y = self.display_class.titlebar_height + 4
        line_h = max(10, self.fonts.small.height + 2)
        font = self.fonts.small.font
        selected_star = self._selected_star()
        completed = align_status.get("completed_points", 0)
        total = align_status.get("total_points", self.align_points)
        message = align_status.get("message") or _("Idle")

        rows = [
            _("Points: {points}").format(points=self.align_points),
            _("Star: {star}").format(star=selected_star["name"]),
            _("Progress: {done}/{total}").format(done=completed, total=total),
            str(message),
        ]
        for row in rows:
            if y + line_h > self.display_class.resY:
                break
            self._draw_text((4, y), row[:28], font=font, fill=self.colors.get(192))
            y += line_h

        hints = [
            _("1 Manual  2 Auto"),
            _("+/- Points  4/6 Star"),
            _("Square Start/Confirm"),
            _("0 Cancel"),
        ]
        hint_y = self.display_class.resY - (len(hints) * line_h) - 2
        for hint in hints:
            self._draw_text(
                (4, hint_y), hint[:28], font=font, fill=self.colors.get(128)
            )
            hint_y += line_h

    def _draw_align_overlay(self):
        align_status = self._align_status()
        current_star = self._current_star()
        completed = align_status.get("completed_points", 0)
        total = align_status.get("total_points", self.align_points)
        star_name = current_star.get("name") or _("Align Star")
        font = self.fonts.base.font
        small_font = self.fonts.small.font
        line_h = self.fonts.base.height + 2
        small_h = self.fonts.small.height + 2
        bright = self.colors.get(192)
        shadow = self.colors.get(0)

        def overlay_text(x, y, text, use_font=font, fill=bright):
            self.draw.text((x + 1, y + 1), text, font=use_font, fill=shadow)
            self.draw.text((x, y), text, font=use_font, fill=fill)

        top_y = self.display_class.titlebar_height + 2
        overlay_text(4, top_y, str(star_name)[:18])
        overlay_text(
            4,
            top_y + line_h,
            _("Point {done}/{total}").format(done=completed + 1, total=total)[:22],
            use_font=small_font,
        )

        bottom_y = self.display_class.resY - (small_h * 4) - 2
        overlay_text(4, bottom_y, _("789 / 4 6 / 123 move"), use_font=small_font)
        overlay_text(4, bottom_y + small_h, _("Release: stop"), use_font=small_font)
        overlay_text(4, bottom_y + small_h * 2, _("+/- Speed"), use_font=small_font)
        overlay_text(
            4, bottom_y + small_h * 3, _("Square confirm"), use_font=small_font
        )

    def update(self, force=False):
        if self._is_adjusting():
            self._send_motion_keepalive()
            self._draw_camera_background()
            self._draw_align_overlay()
            return self.screen_update(title_bar=True, button_hints=False)

        self._draw_setup()
        return self.screen_update()

    def key_number(self, number):
        if self._is_adjusting():
            if number == 0:
                self._send_mount({"type": "multipoint_align_cancel"})
                self.message(_("Align Cancelled"), 1)
            return

        if number == 0:
            self._send_mount({"type": "multipoint_align_cancel"})
            self.message(_("Align Cancelled"), 1)
        elif number == 1:
            self._start_manual()
        elif number == 2:
            self._start_auto()
        elif 1 <= number <= 9:
            self.align_points = clamp_align_points(number)
            self.update()

    def key_number_press(self, number):
        if self._is_adjusting():
            direction = self._number_direction.get(number)
            if direction:
                self._move(direction)
                return
        self.key_number(number)

    def key_number_release(self, number):
        if self._is_adjusting() and number in self._number_direction:
            self._move("stop")

    def key_text(self, char: str):
        if self._is_adjusting():
            super().key_text(char)

    def key_text_press(self, char: str):
        if self._is_adjusting():
            super().key_text_press(char)

    def key_text_release(self, char: str):
        if self._is_adjusting():
            super().key_text_release(char)

    def key_plus(self):
        if self._is_adjusting():
            super().key_plus()
            return
        self.align_points = clamp_align_points(self.align_points + 1)
        self.update()

    def key_minus(self):
        if self._is_adjusting():
            super().key_minus()
            return
        self.align_points = clamp_align_points(self.align_points - 1)
        self.update()

    def key_left(self):
        if self._is_adjusting():
            return False
        self.star_index = (self.star_index - 1) % len(BRIGHT_ALIGN_STARS)
        self.update()
        return False

    def key_right(self):
        if self._is_adjusting():
            return False
        self.star_index = (self.star_index + 1) % len(BRIGHT_ALIGN_STARS)
        self.update()
        return False

    def _start_manual(self):
        star = self._selected_star()
        if self._send_mount(
            {
                "type": "multipoint_align_start",
                "mode": "manual",
                "points": self.align_points,
                "star_name": star["name"],
            }
        ):
            self.message(_("Manual Align"), 1)

    def _start_auto(self):
        if self._send_mount(
            {
                "type": "multipoint_align_start",
                "mode": "auto",
                "points": self.align_points,
            }
        ):
            self.message(_("Auto Align"), 1)

    def key_square(self):
        align_status = self._align_status()
        if align_status.get("active") and align_status.get("current_star"):
            if self._send_mount({"type": "multipoint_align_confirm"}):
                self.message(_("Point Confirmed"), 1)
            return
        if align_status.get("active"):
            star = self._selected_star()
            if self._send_mount(
                {
                    "type": "multipoint_align_select_star",
                    "star_name": star["name"],
                }
            ):
                self.message(_("Align Star"), 1)
            return
        self._start_manual()
