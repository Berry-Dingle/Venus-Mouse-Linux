"""Offline-only Lighting page using the approved Buttons card language."""

from __future__ import annotations

import colorsys
import os
import re
import sys
from dataclasses import dataclass

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, Pango

from ..accessibility import apply_accessible_label
from ..assets import TOP_ARTWORK
from ..buttons.artwork import TOP_POLICY, TextureViewport, texture_from_artwork
from .model import (
    BREATHING_SPEEDS,
    BRIGHTNESS_LEVELS,
    MODES,
    ProfileLightingState,
    palette_swatches,
)

# Fixed measuring width for the preview summary label; see _build_preview_card.
PREVIEW_VALUE_WIDTH_CHARS = 12


def _rgba(rgb: tuple[int, int, int], alpha: float = 1.0) -> Gdk.RGBA:
    colour = Gdk.RGBA()
    colour.parse("#{:02x}{:02x}{:02x}".format(*rgb))
    colour.alpha = alpha
    return colour


def adjust_ring_colour(selected_colour: tuple[int, int, int]) -> tuple[int, int, int]:
    """Lighten only the presentation ring when the selected colour is dark."""
    channels = tuple(min(max(int(channel), 0), 255) / 255 for channel in selected_colour)
    luminance = (
        0.2126 * channels[0]
        + 0.7152 * channels[1]
        + 0.0722 * channels[2]
    )
    if luminance >= 0.65 or (max(channels) >= 0.90 and luminance >= 0.15):
        blend = 0.05
    elif luminance >= 0.25:
        blend = 0.05 + ((0.65 - luminance) / 0.40) * 0.20
    else:
        blend = 0.33 + ((0.25 - luminance) / 0.25) * 0.20
    blend = min(max(blend, 0.0), 0.53)
    return tuple(
        min(255, max(0, round(channel + (255 - channel) * blend)))
        for channel in (min(max(int(value), 0), 255) for value in selected_colour)
    )


@dataclass(frozen=True)
class PickerDragCalculation:
    start_x: float
    start_y: float
    offset_x: float
    offset_y: float
    current_x: float
    current_y: float
    width: float
    height: float
    clamped_x: float
    clamped_y: float


class PickerInteractionModel:
    """Normalized colour state shared by the two local picker gestures."""

    def __init__(self, rgb: tuple[int, int, int], on_change=None) -> None:
        self._on_change = on_change
        self.sync_from_rgb(rgb)

    def sync_from_rgb(self, rgb: tuple[int, int, int]) -> None:
        channels = (channel / 255 for channel in rgb)
        self.hue, self.saturation, self.value = colorsys.rgb_to_hsv(*channels)

    @property
    def rgb(self) -> tuple[int, int, int]:
        return tuple(
            round(channel * 255)
            for channel in colorsys.hsv_to_rgb(self.hue, self.saturation, self.value)
        )

    def update_from_sv_drag(
        self,
        start_x: float,
        start_y: float,
        offset_x: float,
        offset_y: float,
        width: float,
        height: float,
    ) -> PickerDragCalculation:
        width = max(float(width), 1.0)
        height = max(float(height), 1.0)
        current_x = start_x + offset_x
        current_y = start_y + offset_y
        clamped_x = min(max(current_x, 0.0), width)
        clamped_y = min(max(current_y, 0.0), height)
        self.saturation = clamped_x / width
        self.value = 1.0 - (clamped_y / height)
        self._notify()
        return PickerDragCalculation(
            start_x,
            start_y,
            offset_x,
            offset_y,
            current_x,
            current_y,
            width,
            height,
            clamped_x,
            clamped_y,
        )

    def update_from_hue_drag(
        self,
        start_x: float,
        offset_x: float,
        width: float,
        height: float,
    ) -> PickerDragCalculation:
        width = max(float(width), 1.0)
        height = max(float(height), 1.0)
        current_x = start_x + offset_x
        clamped_x = min(max(current_x, 0.0), width)
        self.hue = clamped_x / width
        self._notify()
        return PickerDragCalculation(
            start_x,
            0.0,
            offset_x,
            0.0,
            current_x,
            0.0,
            width,
            height,
            clamped_x,
            height / 2.0,
        )

    def sv_marker(self, width: float, height: float) -> tuple[float, float]:
        return self.saturation * width, (1.0 - self.value) * height

    def hue_marker(self, width: float) -> float:
        return self.hue * width

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self)


def _picker_debug(
    identity: str,
    event: str,
    model: PickerInteractionModel,
    calculation: PickerDragCalculation | None,
) -> None:
    if os.environ.get("VENUS_PICKER_DEBUG") != "1":
        return
    if calculation is None:
        detail = "start=none offsets=none current=none size=none clamped=none"
    else:
        detail = (
            f"start=({calculation.start_x:.2f},{calculation.start_y:.2f}) "
            f"offsets=({calculation.offset_x:.2f},{calculation.offset_y:.2f}) "
            f"current=({calculation.current_x:.2f},{calculation.current_y:.2f}) "
            f"size=({calculation.width:.2f},{calculation.height:.2f}) "
            f"clamped=({calculation.clamped_x:.2f},{calculation.clamped_y:.2f})"
        )
    if calculation is None:
        marker = "marker=none"
    elif identity == "SV":
        marker_x, marker_y = model.sv_marker(calculation.width, calculation.height)
        marker = f"marker=({marker_x:.2f},{marker_y:.2f})"
    else:
        marker = (
            f"marker=({model.hue_marker(calculation.width):.2f},"
            f"{calculation.height / 2.0:.2f})"
        )
    print(
        f"VENUS_PICKER_DEBUG gesture={identity} event={event} {detail} "
        f"hsv=({model.hue:.6f},{model.saturation:.6f},{model.value:.6f}) "
        f"{marker}",
        file=sys.stderr,
        flush=True,
    )


class SaturationValueField(Gtk.DrawingArea):
    def __init__(self, model: PickerInteractionModel) -> None:
        super().__init__()
        self.model = model
        self._drag_start: tuple[float, float] | None = None
        self.set_size_request(-1, 88)
        self.set_can_target(True)
        apply_accessible_label(self, "Saturation and value")
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.set_exclusive(True)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self.connect("unrealize", self._cancel_drag)

    def _pressed(self, _gesture, _n_press: int, x: float, y: float) -> None:
        self.model.update_from_sv_drag(x, y, 0, 0, self.get_width(), self.get_height())

    def _drag_begin(self, gesture, x: float, y: float) -> None:
        self._drag_start = (x, y)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        result = self.model.update_from_sv_drag(
            x, y, 0, 0, self.get_width(), self.get_height()
        )
        _picker_debug("SV", "begin", self.model, result)

    def _drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        if self._drag_start is None:
            return
        result = self.model.update_from_sv_drag(
            self._drag_start[0],
            self._drag_start[1],
            offset_x,
            offset_y,
            self.get_width(),
            self.get_height(),
        )
        _picker_debug("SV", "update", self.model, result)

    def _drag_end(self, _gesture, offset_x: float, offset_y: float) -> None:
        if self._drag_start is not None:
            result = self.model.update_from_sv_drag(
                self._drag_start[0],
                self._drag_start[1],
                offset_x,
                offset_y,
                self.get_width(),
                self.get_height(),
            )
            _picker_debug("SV", "end", self.model, result)
        self._drag_start = None

    def _cancel_drag(self, _widget=None) -> None:
        if self._drag_start is not None:
            _picker_debug("SV", "cancel", self.model, None)
        self._drag_start = None

    def _draw(self, _area, context, width, height) -> None:
        base = colorsys.hsv_to_rgb(self.model.hue, 1, 1)
        for y in range(height):
            for x in range(width):
                saturation = x / max(width - 1, 1)
                value = 1 - y / max(height - 1, 1)
                colour = tuple(
                    (1 - saturation) * value + saturation * channel * value
                    for channel in base
                )
                context.set_source_rgb(*colour)
                context.rectangle(x, y, 1, 1)
                context.fill()
        context.set_source_rgba(0, 0, 0, .32)
        context.rectangle(0, 0, width, height)
        context.fill()
        marker_x, marker_y = self.model.sv_marker(width, height)
        context.set_line_width(1)
        context.set_source_rgb(1, 1, 1)
        context.new_path()
        context.arc(marker_x, marker_y, 10, 0, 6.2832)
        context.stroke()
        context.set_source_rgb(0, 0, 0)
        context.new_path()
        context.arc(marker_x, marker_y, 7, 0, 6.2832)
        context.stroke()


class HueStrip(Gtk.DrawingArea):
    def __init__(self, model: PickerInteractionModel) -> None:
        super().__init__()
        self.model = model
        self._drag_start_x: float | None = None
        self.set_size_request(-1, 35)
        self.set_can_target(True)
        apply_accessible_label(self, "Hue")
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.set_exclusive(True)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        self.connect("unrealize", self._cancel_drag)

    def _pressed(self, _gesture, _n_press: int, x: float, _y: float) -> None:
        self.model.update_from_hue_drag(x, 0, self.get_width(), self.get_height())

    def _drag_begin(self, gesture, x: float, _y: float) -> None:
        self._drag_start_x = x
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        result = self.model.update_from_hue_drag(
            x, 0, self.get_width(), self.get_height()
        )
        _picker_debug("HUE", "begin", self.model, result)

    def _drag_update(self, _gesture, offset_x: float, _offset_y: float) -> None:
        if self._drag_start_x is None:
            return
        result = self.model.update_from_hue_drag(
            self._drag_start_x, offset_x, self.get_width(), self.get_height()
        )
        _picker_debug("HUE", "update", self.model, result)

    def _drag_end(self, _gesture, offset_x: float, _offset_y: float) -> None:
        if self._drag_start_x is not None:
            result = self.model.update_from_hue_drag(
                self._drag_start_x, offset_x, self.get_width(), self.get_height()
            )
            _picker_debug("HUE", "end", self.model, result)
        self._drag_start_x = None

    def _cancel_drag(self, _widget=None) -> None:
        if self._drag_start_x is not None:
            _picker_debug("HUE", "cancel", self.model, None)
        self._drag_start_x = None

    def _draw(self, _area, context, width, height) -> None:
        for x in range(width):
            context.set_source_rgb(*colorsys.hsv_to_rgb(x / max(width - 1, 1), 1, 1))
            context.rectangle(x, 0, 1, height)
            context.fill()
        marker_x = self.model.hue_marker(width)
        context.set_source_rgba(1, 1, 1, .8)
        context.rectangle(marker_x - 2, 0, 4, height)
        context.fill()


class ColourField(Gtk.Box):
    """Visual picker composed of two local, exclusive gesture widgets."""

    def __init__(self, state: ProfileLightingState) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.state = state
        self._publishing_model = False
        self.model = PickerInteractionModel(state.stored_colour, self._model_changed)
        self.set_size_request(-1, 130)
        self.set_focusable(True)
        apply_accessible_label(self, "Custom colour picker")
        self._sv_field = SaturationValueField(self.model)
        self._hue_strip = HueStrip(self.model)
        self.append(self._sv_field)
        gap = Gtk.Box()
        gap.set_size_request(-1, 12)
        gap.set_can_target(False)
        self.append(gap)
        self.append(self._hue_strip)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def _model_changed(self, model: PickerInteractionModel) -> None:
        self._publishing_model = True
        try:
            self.state.select_colour(model.rgb)
        finally:
            self._publishing_model = False
        self._sv_field.queue_draw()
        self._hue_strip.queue_draw()

    def sync_from_state(self, rgb: tuple[int, int, int]) -> None:
        if not self._publishing_model:
            self.model.sync_from_rgb(rgb)
        self._sv_field.queue_draw()
        self._hue_strip.queue_draw()

    def _key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        hue = self.model.hue
        saturation = self.model.saturation
        value = self.model.value
        step = .03
        if keyval == Gdk.KEY_Left:
            saturation = max(0, saturation - step)
        elif keyval == Gdk.KEY_Right:
            saturation = min(1, saturation + step)
        elif keyval == Gdk.KEY_Up:
            value = min(1, value + step)
        else:
            value = max(0, value - step)
        self.model.hue = hue
        self.model.saturation = saturation
        self.model.value = value
        self._model_changed(self.model)
        return True


def _rounded_rect(context, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2, height / 2)
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -1.5708, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, 1.5708)
    context.arc(x + radius, y + height - radius, radius, 1.5708, 3.1416)
    context.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
    context.close_path()


class PreviewBackdrop(Gtk.DrawingArea):
    def __init__(self, state: ProfileLightingState) -> None:
        super().__init__()
        self.state = state
        self.set_can_target(False)
        self.set_draw_func(self._draw)

    def _draw(self, _area, context, width, height) -> None:
        r, g, b = (channel / 255 for channel in self.state.colour)
        # This backdrop exists only on LightingPage and therefore cannot tint
        # the approved Buttons preview. A single gradient avoids visible rings.
        centre_x = width / 2
        centre_y = height / 2
        radius = min(width, height) * .50
        glow = cairo.RadialGradient(centre_x, centre_y, 0, centre_x, centre_y, radius)
        glow.add_color_stop_rgba(0.0, r, g, b, .14)
        glow.add_color_stop_rgba(.35, r, g, b, .11)
        glow.add_color_stop_rgba(.72, r, g, b, .05)
        glow.add_color_stop_rgba(1.0, r, g, b, 0.0)
        context.set_source(glow)
        context.rectangle(0, 0, width, height)
        context.fill()


class PreviewHighlights(Gtk.DrawingArea):
    def __init__(self, state: ProfileLightingState) -> None:
        super().__init__()
        self.state = state
        self.set_can_target(False)
        self.set_draw_func(self._draw)

    def _draw(self, _area, context, width, height) -> None:
        r, g, b = (channel / 255 for channel in self.state.colour)
        ring_r, ring_g, ring_b = (
            channel / 255 for channel in adjust_ring_colour(self.state.colour)
        )
        # Presentation tint stays over the two confirmed illuminated areas;
        # the shapes follow the physical wheel and side-button strip.
        wheel_x = width * .50 - width * .033 - 1
        wheel_y = height * .235 - 34
        wheel_width = width * .066
        wheel_height = height * .155 - 10
        _rounded_rect(context, wheel_x, wheel_y, wheel_width, wheel_height, 7)
        context.set_source_rgba(r, g, b, .20)
        context.fill()

        circle_x = width * .50 + 10
        circle_y = height * .78 + 25
        circle_radius = width * .045
        glow = cairo.RadialGradient(
            circle_x,
            circle_y,
            circle_radius * .25,
            circle_x,
            circle_y,
            circle_radius * 2.5,
        )
        glow.add_color_stop_rgba(0.0, r, g, b, .23)
        glow.add_color_stop_rgba(.35, r, g, b, .18)
        glow.add_color_stop_rgba(.72, r, g, b, .065)
        glow.add_color_stop_rgba(1.0, r, g, b, 0.0)
        context.set_source(glow)
        context.arc(circle_x, circle_y, circle_radius * 2.5, 0, 6.2832)
        context.fill()
        context.set_source_rgba(ring_r, ring_g, ring_b, .18)
        context.set_line_width(1)
        context.arc(circle_x, circle_y, circle_radius, 0, 6.2832)
        context.stroke()


class LightingPage(Gtk.Box):
    """Lighting editor bound to the selected profile."""

    def __init__(self, workspace) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page")
        self.workspace = workspace
        self.presentation = ProfileLightingState(workspace)
        self.swatches = palette_swatches()
        self._syncing = False
        self._mouse_live = False
        self._install_swatch_css()
        self.presentation.subscribe(self._refresh)
        workspace.subscribe(self._workspace_changed)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.append(body)
        body.append(self._build_preview_card())
        body.append(self._build_controls_card())
        self._refresh(self.presentation)

    def _install_swatch_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        rules = "\n".join(
            f".{swatch.stable_id} {{ background: {swatch.hex_value}; }}" for swatch in self.swatches
        )
        provider = Gtk.CssProvider()
        provider.load_from_string(rules)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )

    def _workspace_changed(self, reason: str) -> None:
        if reason != "lighting":
            self.presentation.changed()

    # ------------------------------------------------------------ building
    def _build_preview_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("card-panel")
        title = Gtk.Label(label="Lighting Preview", xalign=0)
        title.add_css_class("panel-title")
        card.append(title)
        stage = Gtk.Overlay()
        stage.add_css_class("artwork-region")
        stage.add_css_class("artwork-stage")
        stage.set_size_request(TOP_POLICY.max_width, TOP_POLICY.max_height)
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        self._preview_backdrop = PreviewBackdrop(self.presentation)
        self._preview_highlights = PreviewHighlights(self.presentation)
        stage.set_child(self._preview_backdrop)
        stage.add_overlay(TextureViewport(texture_from_artwork(TOP_ARTWORK), TOP_POLICY))
        stage.add_overlay(self._preview_highlights)
        card.append(stage)
        # The summary text embeds the live #RRGGBB value, which changes on every
        # picker drag step. A wrapping label is height-for-width: the
        # horizontal page box measures it *for the row's height*, and that
        # width follows the text, so each new hex shifted the split between
        # the two cards by a few pixels. A single-line, ellipsizing label with
        # width-chars == max-width-chars has a constant-size request that is
        # independent of its text; it simply fills the card's allocated width.
        self._preview_value = Gtk.Label(xalign=0)
        self._preview_value.set_wrap(False)
        self._preview_value.set_ellipsize(Pango.EllipsizeMode.END)
        self._preview_value.set_width_chars(PREVIEW_VALUE_WIDTH_CHARS)
        self._preview_value.set_max_width_chars(PREVIEW_VALUE_WIDTH_CHARS)
        self._preview_value.set_hexpand(True)
        self._preview_value.add_css_class("lighting-preview-value")
        card.append(self._preview_value)
        return card

    def _build_controls_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card-panel")
        card.set_size_request(414, -1)
        title = Gtk.Label(label="Lighting Controls", xalign=0)
        title.add_css_class("panel-title")
        card.append(title)

        mode_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        mode_section.add_css_class("lighting-mode-container")
        mode_section.append(Gtk.Label(label="Lighting Mode", xalign=0))
        self._mode_labels = [item.label for item in MODES]
        self._mode_dropdown = Gtk.DropDown.new_from_strings(self._mode_labels)
        self._mode_dropdown.connect("notify::selected", self._mode_changed)
        apply_accessible_label(self._mode_dropdown, "Lighting mode")
        mode_section.append(self._mode_dropdown)
        card.append(mode_section)

        level_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        level_controls.add_css_class("lighting-level-controls")
        brightness = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brightness.add_css_class("lighting-control-row")
        brightness.set_hexpand(True)
        brightness.append(Gtk.Label(label="Brightness", xalign=0))
        self._brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 3, 1)
        self._brightness.set_hexpand(True)
        self._brightness.set_draw_value(False)
        self._brightness.set_digits(0)
        for level, label in BRIGHTNESS_LEVELS:
            self._brightness.add_mark(level, Gtk.PositionType.BOTTOM, label)
        self._brightness.connect("value-changed", self._brightness_changed)
        apply_accessible_label(self._brightness, "Brightness")
        brightness.append(self._brightness)
        level_controls.append(brightness)

        speed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        speed.add_css_class("lighting-control-row")
        speed.set_hexpand(True)
        speed.append(Gtk.Label(label="Breathing Speed", xalign=0))
        self._speed_values = [raw for raw, _label in BREATHING_SPEEDS]
        self._speed = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, len(self._speed_values) - 1, 1
        )
        self._speed.set_hexpand(True)
        self._speed.set_draw_value(False)
        self._speed.set_digits(0)
        for index, (_raw, label) in enumerate(BREATHING_SPEEDS):
            self._speed.add_mark(index, Gtk.PositionType.BOTTOM, label)
        self._speed.connect("value-changed", self._speed_changed)
        apply_accessible_label(self._speed, "Breathing speed")
        speed.append(self._speed)
        level_controls.append(speed)
        card.append(level_controls)

        swatch_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        swatch_section.add_css_class("section-card")
        swatch_section.add_css_class("lighting-swatch-section")
        swatch_section.set_hexpand(False)
        swatch_section.append(Gtk.Label(label="Palette", xalign=0))
        grid = Gtk.Grid(column_spacing=3, row_spacing=3)
        grid.add_css_class("lighting-swatch-grid")
        self._swatch_buttons: dict[str, Gtk.Button] = {}
        for index, swatch in enumerate(self.swatches):
            button = Gtk.Button()
            button.add_css_class("lighting-swatch")
            button.add_css_class("palette-swatch")
            button.add_css_class(swatch.stable_id)
            button.set_size_request(24, 22)
            button.set_tooltip_text(f"{swatch.name} {swatch.hex_value}")
            apply_accessible_label(button, f"{swatch.name}, {swatch.hex_value}")
            button.connect("clicked", self._swatch_clicked, swatch.stable_id)
            self._swatch_buttons[swatch.stable_id] = button
            grid.attach(button, index % 6, index // 6, 1, 1)
        swatch_section.append(grid)
        self._swatch_section = swatch_section

        picker_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        picker_section.add_css_class("section-card")
        picker_section.add_css_class("lighting-picker-section")
        picker_section.set_hexpand(True)
        picker_section.append(Gtk.Label(label="Custom Colour", xalign=0))
        self._colour_field = ColourField(self.presentation)
        picker_section.append(self._colour_field)
        current = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        current.add_css_class("lighting-picker-current")
        self._colour_preview = Gtk.DrawingArea()
        self._colour_preview.set_size_request(30, 24)
        self._colour_preview.set_draw_func(self._draw_colour_preview)
        current.append(self._colour_preview)
        self._colour_value = Gtk.Entry()
        self._colour_value.set_hexpand(True)
        self._colour_value.set_max_length(7)
        self._colour_value.set_placeholder_text("#RRGGBB")
        self._colour_value.set_tooltip_text("Type a colour as #RRGGBB and press Enter")
        apply_accessible_label(self._colour_value, "Custom colour hex value")
        self._colour_value.connect("activate", self._colour_entry_activate)
        entry_focus = Gtk.EventControllerFocus()
        entry_focus.connect("leave", self._colour_entry_focus_leave)
        self._colour_value.add_controller(entry_focus)
        current.append(self._colour_value)
        picker_section.append(current)
        self._picker_section = picker_section

        colour_controls_row = Gtk.Grid(column_spacing=8)
        colour_controls_row.add_css_class("colour-controls-row")
        colour_controls_row.set_hexpand(True)
        self._colour_controls_row = colour_controls_row
        swatch_section.set_valign(Gtk.Align.FILL)
        picker_section.set_valign(Gtk.Align.FILL)
        colour_controls_row.attach(swatch_section, 0, 0, 1, 1)
        colour_controls_row.attach(picker_section, 1, 0, 1, 1)
        card.append(colour_controls_row)

        self._colour_note = Gtk.Label(xalign=0, wrap=True)
        self._colour_note.add_css_class("dim-label")
        self._colour_note.add_css_class("lighting-note")
        card.append(self._colour_note)
        return card

    # ------------------------------------------------------------ mouse
    LIVE_NOTE = "Changes are sent to the mouse straight away and stay on it."
    OFFLINE_NOTE = "Changes are kept in the app only until the mouse is read."

    def mouse_note(self) -> str:
        return self.LIVE_NOTE if self._mouse_live else self.OFFLINE_NOTE

    def set_mouse_live(self, live: bool) -> None:
        """Whether lighting edits currently reach the mouse (set by the window)."""
        if live != self._mouse_live:
            self._mouse_live = live
            self._refresh(self.presentation)

    # ------------------------------------------------------------ handlers
    def _mode_changed(self, dropdown, _param) -> None:
        if self._syncing:
            return
        index = dropdown.get_selected()
        if 0 <= index < len(MODES):
            self.presentation.select_mode(MODES[index].stable_id)

    def _swatch_clicked(self, _button, stable_id: str) -> None:
        self.presentation.select_swatch(stable_id)

    def _brightness_changed(self, scale) -> None:
        if self._syncing:
            return
        level = round(scale.get_value())
        if scale.get_value() != level:
            scale.set_value(level)
            return
        self.presentation.set_brightness(level)

    def _speed_changed(self, scale) -> None:
        if self._syncing:
            return
        index = round(scale.get_value())
        if scale.get_value() != index:
            scale.set_value(index)
            return
        self.presentation.set_breathing_speed(self._speed_values[index])

    def _draw_colour_preview(self, _area, context, width, height) -> None:
        colour = _rgba(self.presentation.stored_colour)
        context.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
        context.rectangle(0, 0, width, height)
        context.fill()

    def _colour_entry_activate(self, _entry: Gtk.Entry) -> None:
        self._commit_colour_entry()

    def _colour_entry_focus_leave(self, _controller: Gtk.EventControllerFocus) -> None:
        self._commit_colour_entry()

    def _commit_colour_entry(self) -> None:
        value = self._colour_value.get_text().strip()
        if not value.startswith("#"):
            value = "#" + value
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            self.presentation.select_colour(
                tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
            )
            self._colour_value.remove_css_class("error")
        elif self._colour_value.get_text().strip():
            self._colour_value.add_css_class("error")
        self._sync_colour_entry(self.presentation.colour_hex)

    def _sync_colour_entry(self, canonical: str) -> None:
        if self._colour_value.get_text() == canonical:
            return
        if self._colour_value.has_focus() and "error" in self._colour_value.get_css_classes():
            return
        self._colour_value.set_text(canonical)

    # ------------------------------------------------------------ refresh
    def _refresh(self, state: ProfileLightingState) -> None:
        self._syncing = True
        try:
            mode = state.mode
            if mode is not None:
                self._mode_dropdown.set_selected(MODES.index(mode))
            else:
                self._mode_dropdown.set_selected(Gtk.INVALID_LIST_POSITION)
            self._brightness.set_value(min(max(state.brightness_level, 0), 3))
            speed = state.breathing_speed
            if speed in self._speed_values:
                self._speed.set_value(self._speed_values.index(speed))
            self._speed.set_sensitive(state.breathing_relevant)
            colour_ok = state.colour_relevant
            self._swatch_section.set_sensitive(colour_ok)
            self._picker_section.set_sensitive(colour_ok)
            stored = state.profile.led_color_raw
            for swatch in self.swatches:
                button = self._swatch_buttons[swatch.stable_id]
                if stored == swatch.colorref:
                    button.add_css_class("selected-lighting-swatch")
                else:
                    button.remove_css_class("selected-lighting-swatch")
            self._sync_colour_entry(state.colour_hex)
            self._colour_field.sync_from_state(state.stored_colour)
        finally:
            self._syncing = False
        mode_label = mode.label if mode else f"Unknown mode {state.mode_raw} (preserved)"
        brightness = dict(BRIGHTNESS_LEVELS).get(state.brightness_level, f"raw {state.brightness_level}")
        self._preview_value.set_text(
            f"Profile {self.workspace.selected_slot}  ·  {mode_label}  ·  {state.colour_hex}  ·  Brightness {brightness}"
        )
        if not colour_ok:
            note = f"{mode_label} does not use the colour setting."
        elif speed not in self._speed_values and state.breathing_relevant:
            note = f"Breathing speed value {speed} is preserved; choose a speed to replace it."
        else:
            note = ""
        # One sentence per line: a single long line widened the window (Rainbow/Off).
        self._colour_note.set_text("\n".join(part for part in (note, self.mouse_note()) if part))
        self._colour_preview.queue_draw()
        self._preview_backdrop.queue_draw()
        self._preview_highlights.queue_draw()
