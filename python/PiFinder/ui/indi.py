#!/usr/bin/python
# -*- coding:utf-8 -*-

import json
import time

from PIL import ImageChops

from PiFinder import utils
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
            self._draw_text((6, y), _("Speed: {rate} {label}").format(rate=rate, label=label))
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

        hints = [
            _("1 Init"),
            _("2 Time/Loc"),
            _("3 Park  6 Unpark"),
            _("4 Home  5 Return"),
            _("7 Set-Park"),
        ]
        hint_y = max(y + 4, self.display_class.resY - (len(hints) * line_h) - 2)
        for hint in hints:
            self._draw_text((6, hint_y), hint, fill=self.colors.get(128))
            hint_y += line_h

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


class UIIndiGuide(UIIndiBase):
    __title__ = "INDI GUIDE"

    _number_direction = {
        1: "southwest",
        2: "south",
        3: "southeast",
        4: "west",
        5: "stop",
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
        bottom_hint_y = self.display_class.resY - (line_h * 3) - 2
        bottom_key_y = max(center_y + line_h + 2, bottom_hint_y - line_h - 2)

        centered(top_y, "7 8 9")
        centered(bottom_key_y, "1 2 3")
        overlay_text(4, center_y, "4")
        right_w, _height = text_size("6")
        overlay_text(self.display_class.resX - right_w - 4, center_y, "6")

        overlay_text(4, bottom_hint_y, "5:stop")
        overlay_text(
            4,
            bottom_hint_y + line_h,
            _("+/-:Speed {rate} {label}").format(rate=slew_rate, label=label),
        )
        overlay_text(4, bottom_hint_y + (line_h * 2), _("Square:align"))

    def update(self, force=False):
        self._draw_camera_background()
        self._draw_keypad_overlay()
        return self.screen_update(title_bar=True, button_hints=False)

    def _move(self, direction):
        if direction == "stop":
            self._send_mount({"type": "stop_movement"})
        else:
            self._send_mount({"type": "manual_movement", "direction": direction})
        self.update(force=True)

    def key_number(self, number):
        direction = self._number_direction.get(number)
        if direction:
            self._move(direction)

    def key_text(self, char: str):
        direction = self._text_direction.get(char.lower())
        if direction:
            self._move(direction)

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
