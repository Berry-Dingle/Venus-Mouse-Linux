"""Per-profile pointer, scrolling and polling settings (Windows "General" tab).

Ranges come from controlled official-editor fixtures (``FORMAT_SPEC.md``):
pointer speed ``MouseSensitivity`` 1..20, acceleration 1..4, scroll speed
``WheelScrollLines`` 1..63, double-click speed 1..10 and polling-rate enum
8/4/2/1 = 125/250/500/1000 Hz.  Unknown stored values are shown and kept
until the user chooses a replacement.  No device access happens here.
"""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from .accessibility import apply_accessible_label
from ..pfd import (
    DOUBLE_CLICK_SPEED_MAX,
    DOUBLE_CLICK_SPEED_MIN,
    POINTER_SPEED_MAX,
    POINTER_SPEED_MIN,
    SCROLL_SPEED_MAX,
    SCROLL_SPEED_MIN,
    POLLING_ENUM_TO_HZ,
    PollingRateHz,
)

POLLING_CHOICES = (PollingRateHz.HZ_125, PollingRateHz.HZ_250, PollingRateHz.HZ_500, PollingRateHz.HZ_1000)
DEVICE_STATUS_TEXT = (
    "When the app starts it reads the mouse's settings, then follows the "
    "mouse's DPI and profile buttons as you press them. Lighting and the active "
    "profile are sent to the mouse as soon as you change them. DPI, polling, "
    "scroll and button changes are sent when you click Apply (all five profiles). Putting a "
    "macro in one of the 15 device slots (or removing it) uploads it straight away."
)
MOUSE_ACCESS_TEXT = (
    "Not needed with packages. AppImage or source: paste into a terminal, then "
    "replug the mouse. Needs systemd (Void, Artix, Alpine need their own setup). "
    "Nothing here is run by the app."
)
# Logged-in-user access to the Venus configuration interface only (04d9:fc55,
# interface 02), via systemd's uaccess tag; no group needed.
# The right column has a fixed width and its wrapping labels ask for only a few
# characters of natural width (each still fills and wraps to the column), so
# the column split does not depend on text length. "Set up mouse access" also
# keeps the card within the fixed window height (see _build_mouse_access).
RIGHT_COLUMN_WIDTH = 620
WRAP_LABEL_WIDTH_CHARS = 30
MOUSE_ACCESS_RULE_PATH = "/etc/udev/rules.d/70-utechsmart-venus.rules"
#: The rule file, line by line (no line contains a single quote).
MOUSE_ACCESS_RULE_LINES = (
    "# UtechSmart Venus 04d9:fc55 configuration HID only: access for the logged-in user.",
    'ACTION!="add|change", GOTO="utechsmart_venus_end"',
    'SUBSYSTEM!="hidraw", GOTO="utechsmart_venus_end"',
    'IMPORT{builtin}="usb_id"',
    'ENV{ID_VENDOR_ID}=="04d9", ENV{ID_MODEL_ID}=="fc55", ATTRS{bInterfaceNumber}=="02", '
    'TAG+="uaccess", MODE="0660"',
    'LABEL="utechsmart_venus_end"',
)
# printf + single quotes + backslash continuations: the same in bash, zsh and
# fish (a heredoc fails in fish). Writes exactly MOUSE_ACCESS_RULE_LINES.
MOUSE_ACCESS_COMMAND = "\n".join((
    "printf '%s\\n' \\",
    *(f"  '{line}' \\" for line in MOUSE_ACCESS_RULE_LINES),
    f"  | sudo tee {MOUSE_ACCESS_RULE_PATH} >/dev/null",
    "sudo udevadm control --reload-rules",
))


# Settings the Windows utility applies to the PC, not to the mouse (hid.exe
# 0x4109d0 SPI_SETMOUSE/SPI_SETMOUSESPEED, SetDoubleClickTime). Here they are the
# desktop's own mouse settings (desktop_settings.py): shown as the desktop has
# them, changed on Apply, hidden when the desktop has no such setting.
PC_SETTING_TOOLTIP = (
    "A desktop setting: it shows your desktop's current value and changes your "
    "desktop's mouse settings when you press Apply. It is not sent to the mouse."
)


class _SliderRow(Gtk.Box):
    def __init__(self, title: str, minimum: int, maximum: int, on_change, marks=(),
                 *, pc_setting: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("settings-row")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("settings-row-title")
        header.append(label)
        self.pc_tag = None
        if pc_setting:
            self.pc_tag = Gtk.Label(label="Desktop")
            self.pc_tag.add_css_class("settings-pc-tag")
            self.pc_tag.set_valign(Gtk.Align.CENTER)
            self.pc_tag.set_tooltip_text(PC_SETTING_TOOLTIP)
            header.append(self.pc_tag)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)
        self.value_label = Gtk.Label(xalign=1)
        self.value_label.add_css_class("settings-row-value")
        header.append(self.value_label)
        self.append(header)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 1)
        self.scale.set_draw_value(False)
        self.scale.set_round_digits(0)
        self.scale.set_hexpand(True)
        for value, text in marks:
            self.scale.add_mark(value, Gtk.PositionType.BOTTOM, text)
        apply_accessible_label(self.scale, title)
        self.scale.connect("value-changed", on_change)
        self.append(self.scale)


class SettingsPage(Gtk.Box):
    def __init__(self, workspace) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.add_css_class("page")
        self.workspace = workspace
        self._syncing = False
        workspace.subscribe(self._workspace_changed)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left.add_css_class("card-panel")
        left.set_hexpand(True)
        title = Gtk.Label(label="Pointer and Scrolling", xalign=0)
        title.add_css_class("panel-title")
        left.append(title)
        self._pointer = _SliderRow("Pointer speed", POINTER_SPEED_MIN, POINTER_SPEED_MAX,
                                   self._pointer_changed, ((1, "Slow"), (10, "Default"), (20, "Fast")),
                                   pc_setting=True)
        # Two stops: Linux desktops have only flat (off) or adaptive (on) acceleration.
        self._acceleration = _SliderRow("Acceleration", 0, 1, self._acceleration_changed,
                                        ((0, "Off"), (1, "On")), pc_setting=True)
        self._acceleration.scale.set_tooltip_text("Off: the pointer moves the same distance at any speed "
                                                  "(flat). On: faster movements go further (adaptive).")
        self._scroll = _SliderRow("Scroll speed (lines per notch)", SCROLL_SPEED_MIN, SCROLL_SPEED_MAX,
                                  self._scroll_changed, ((1, "1"), (63, "63")))
        self._double_click = _SliderRow("Double-click speed", DOUBLE_CLICK_SPEED_MIN, DOUBLE_CLICK_SPEED_MAX,
                                        self._double_click_changed, ((1, "Slow"), (10, "Fast")),
                                        pc_setting=True)
        for row in (self._pointer, self._acceleration, self._scroll, self._double_click):
            left.append(row)
        note = Gtk.Label(xalign=0, wrap=True)
        note.add_css_class("dim-label")
        note.add_css_class("settings-note")
        left.append(note)
        self._note = note
        self.set_desktop(None, frozenset())
        self.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        right.set_size_request(RIGHT_COLUMN_WIDTH, -1)
        polling_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        polling_card.add_css_class("card-panel")
        polling_title = Gtk.Label(label="Polling Rate", xalign=0)
        polling_title.add_css_class("panel-title")
        polling_card.append(polling_title)
        polling_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        polling_row.add_css_class("polling-choices")
        polling_row.set_homogeneous(True)
        self._polling_buttons: dict[int, Gtk.ToggleButton] = {}
        group = None
        for rate in POLLING_CHOICES:
            button = Gtk.ToggleButton(label=f"{int(rate)} Hz")
            button.add_css_class("polling-choice")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.connect("toggled", self._polling_toggled, int(rate))
            apply_accessible_label(button, f"Polling rate {int(rate)} Hz")
            self._polling_buttons[int(rate)] = button
            polling_row.append(button)
        polling_card.append(polling_row)
        self._polling_note = Gtk.Label(xalign=0, wrap=True, max_width_chars=WRAP_LABEL_WIDTH_CHARS)
        self._polling_note.add_css_class("dim-label")
        polling_card.append(self._polling_note)
        right.append(polling_card)

        device_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        device_card.add_css_class("card-panel")
        device_card.set_vexpand(True)
        device_title = Gtk.Label(label="Mouse Connection", xalign=0)
        device_title.add_css_class("panel-title")
        device_card.append(device_title)
        # Two short lines: one long line widened the card and shifted the column split.
        status = Gtk.Label(label="Live: lighting · profile · macro slots\nApply: DPI · polling · scroll · buttons", xalign=0)
        status.add_css_class("device-status-badge")
        device_card.append(status)
        explanation = Gtk.Label(label=DEVICE_STATUS_TEXT, xalign=0, wrap=True, max_width_chars=WRAP_LABEL_WIDTH_CHARS)
        explanation.add_css_class("dim-label")
        self._preview_button = Gtk.Button(label="Show Apply Preview…")
        self._preview_button.add_css_class("action-button")
        self._preview_button.set_tooltip_text(
            "Show the offline list of device writes this profile set would need (nothing is sent)"
        )
        self._preview_button.connect("clicked", self._show_apply_preview)
        device_card.append(self._preview_button)
        access = self._build_mouse_access()
        device_card.append(access)
        # Under the buttons, where the setup commands open: the buttons never
        # move when the commands open or close.
        access.insert_child_after(explanation, self._access_toggle)
        # While the setup commands are open the explanation steps aside, so the
        # card fits the fixed window height (otherwise GTK widened the card).
        # It hides as soon as opening starts but returns only once the close
        # animation has finished; returning at the start made the card briefly
        # too tall, so it jumped wider and back while closing.
        self._explanation = explanation
        self._access_toggle.connect("notify::active", self._access_toggled)
        self._access_revealer.connect("notify::child-revealed", self._access_revealed)
        right.append(device_card)
        self.append(right)
        self._refresh()

    # ------------------------------------------------------------ desktop settings
    def set_desktop(self, name: str | None, available, scope_note: str = "") -> None:
        """Show only the desktop settings this desktop has (``desktop_settings`` names)."""
        from ..desktop_settings import ACCELERATION, DOUBLE_CLICK, POINTER
        rows = {POINTER: self._pointer, ACCELERATION: self._acceleration, DOUBLE_CLICK: self._double_click}
        for key, row in rows.items():
            row.set_visible(key in available)
        text = "Scroll speed is stored on the mouse and sent on Apply."
        if available:
            text += (f" Settings marked Desktop are your {name} mouse settings: they show what the desktop "
                     "has now and change it when you press Apply.")
            if scope_note:
                text += " " + scope_note
        else:
            text += (" Pointer speed, acceleration and double-click speed are desktop settings; "
                     + (f"{name} doesn't offer them here" if name else "they are available on KDE Plasma "
                        "(Wayland) and GNOME")
                     + ", so they are hidden.")
        self._note.set_text(text)

    # ------------------------------------------------------------ mouse access
    def _build_mouse_access(self) -> Gtk.Box:
        """Copy-paste udev setup for AppImage/source users (the app runs nothing)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._access_toggle = Gtk.ToggleButton(label="Set up mouse access…")
        self._access_toggle.add_css_class("action-button")
        self._access_toggle.set_tooltip_text("Show the commands that let your user read the mouse")
        box.append(self._access_toggle)
        self._access_revealer = Gtk.Revealer()
        self._access_toggle.bind_property("active", self._access_revealer, "reveal-child", 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        text = Gtk.Label(label=MOUSE_ACCESS_TEXT, xalign=0, wrap=True)
        text.set_max_width_chars(WRAP_LABEL_WIDTH_CHARS)
        text.add_css_class("dim-label")
        text.add_css_class("settings-note")
        content.append(text)
        command = Gtk.Label(label=MOUSE_ACCESS_COMMAND, xalign=0, selectable=True)
        command.add_css_class("access-command-text")
        apply_accessible_label(command, "Commands to allow your user to read the mouse")
        # The bordered frame stays put; only the text scrolls sideways inside
        # it. A classic (non-overlay) scrollbar gets its own row under the
        # text and uses the app's slim scrollbar style.
        # ALWAYS: GTK only counts a classic scrollbar's row in the size request
        # when it is always shown (AUTOMATIC let it cover the last lines). The
        # long udev line is wider than the column anyway.
        command_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.ALWAYS,
                                              vscrollbar_policy=Gtk.PolicyType.NEVER)
        command_scroller.set_overlay_scrolling(False)
        command_scroller.set_child(command)
        command_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        command_frame.add_css_class("access-command")
        command_frame.set_overflow(Gtk.Overflow.HIDDEN)
        command_frame.append(command_scroller)
        content.append(command_frame)
        self._copy_access = Gtk.Button(label="Copy")
        self._copy_access.add_css_class("action-button")
        self._copy_access.set_halign(Gtk.Align.END)          # under the commands, on the right
        self._copy_access.connect("clicked", self._copy_access_command)
        content.append(self._copy_access)
        self._access_revealer.set_child(content)
        box.append(self._access_revealer)
        return box

    def _access_toggled(self, toggle, _pspec) -> None:
        if toggle.get_active():
            self._explanation.set_visible(False)
        elif not self._access_revealer.get_child_revealed():
            self._explanation.set_visible(True)

    def _access_revealed(self, revealer, _pspec) -> None:
        if not revealer.get_child_revealed() and not self._access_toggle.get_active():
            self._explanation.set_visible(True)

    def show_mouse_access(self) -> None:
        self._access_toggle.set_active(True)

    def _copy_access_command(self, button: Gtk.Button) -> None:
        provider = Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(MOUSE_ACCESS_COMMAND.encode("utf-8")))
        button.get_clipboard().set_content(provider)
        button.set_label("Copied")
        GLib.timeout_add_seconds(2, lambda: (button.set_label("Copy"), GLib.SOURCE_REMOVE)[1])

    # ------------------------------------------------------------ helpers
    @property
    def profile(self):
        return self.workspace.profile()

    def _workspace_changed(self, reason: str) -> None:
        if reason != "settings":
            self._refresh()

    def _edited(self) -> None:
        self.workspace.edited("settings")
        self._refresh()

    def _scale_value(self, row: _SliderRow) -> int:
        return round(row.scale.get_value())

    # ------------------------------------------------------------ handlers
    def _pointer_changed(self, _scale) -> None:
        if self._syncing:
            return
        value = self._scale_value(self._pointer)
        if self.profile.pointer_speed_raw != value:
            self.profile.set_pointer_speed(value)
            self._edited()

    def _acceleration_changed(self, _scale) -> None:
        if self._syncing:
            return
        from ..desktop_settings import acceleration_level_for
        value = acceleration_level_for(self._scale_value(self._acceleration) == 1, self.profile.acceleration)
        if self.profile.acceleration_raw != value:
            self.profile.set_acceleration(value)
            self._edited()

    def _scroll_changed(self, _scale) -> None:
        if self._syncing:
            return
        value = self._scale_value(self._scroll)
        if self.profile.scroll_speed_raw != value:
            self.profile.set_scroll_speed(value)
            self._edited()

    def _double_click_changed(self, _scale) -> None:
        if self._syncing:
            return
        value = self._scale_value(self._double_click)
        if self.profile.double_click_speed_raw != value:
            self.profile.set_double_click_speed(value)
            self._edited()

    def _polling_toggled(self, button: Gtk.ToggleButton, rate: int) -> None:
        if self._syncing or not button.get_active():
            return
        if self.profile.polling_rate_hz != rate:
            self.profile.set_polling_rate_hz(rate)
            self._edited()

    def _show_apply_preview(self, _button) -> None:
        from .apply_preview import present_apply_preview
        present_apply_preview(self.get_root(), self.workspace)

    # ------------------------------------------------------------ refresh
    def _refresh(self) -> None:
        profile = self.profile
        self._syncing = True
        try:
            self._sync_row(self._pointer, profile.pointer_speed_raw, profile.pointer_speed,
                           lambda value: str(value))
            level = profile.acceleration
            self._acceleration.scale.set_value(1 if level and level >= 2 else 0)
            self._acceleration.value_label.set_text("On" if level and level >= 2 else "Off")
            self._sync_row(self._scroll, profile.scroll_speed_raw, profile.scroll_speed,
                           lambda value: f"{value} line{'s' if value != 1 else ''}")
            self._sync_row(self._double_click, profile.double_click_speed_raw, profile.double_click_speed,
                           lambda value: f"{value} · {900 - value * 70} ms")
            raw = profile.polling_rate_raw
            rate = POLLING_ENUM_TO_HZ.get(raw)
            for value, button in self._polling_buttons.items():
                button.set_active(rate is not None and int(rate) == value)
            if rate is None:
                self._polling_note.set_text(f"Stored polling value {raw} is not a known rate; it is kept until you choose one.")
            else:
                self._polling_note.set_text(f"Profile {self.workspace.selected_slot} reports at {int(rate)} Hz.")
        finally:
            self._syncing = False

    @staticmethod
    def _sync_row(row: _SliderRow, raw: int, known: int | None, formatter) -> None:
        if known is None:
            row.value_label.set_text(f"Unknown ({raw})")
            row.value_label.add_css_class("warning-text")
        else:
            row.value_label.set_text(formatter(known))
            row.value_label.remove_css_class("warning-text")
            row.scale.set_value(known)
