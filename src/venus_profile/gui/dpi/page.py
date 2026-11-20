"""DPI stage editor bound to the selected profile."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..accessibility import apply_accessible_label, set_accessible_selected
from .model import DPI_RAW_MAX, DPI_RAW_MIN, DpiRuleError, ProfileDpiState, StageView
from ...pfd import dpi_raw_to_dpi

DPI_STAGE_SIDE_PADDING = 40
DPI_STAGE_WIDTH = 115
DPI_STAGE_GAP = 58
DPI_SLIDER_HEIGHT = 70
DPI_STAGE_TOP_MARGIN = 28
DPI_STAGE_BOTTOM_MARGIN = 28


class ActiveStageLight(Gtk.DrawingArea):
    """Fixed-size indicator for the profile's default (start-up) stage."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(10, 10)
        self.set_content_width(10)
        self.set_content_height(10)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.set_margin_start(8)
        self.set_can_target(False)
        self._active = False
        self.set_draw_func(self._draw)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.queue_draw()

    def _draw(self, _area, context, width, height) -> None:
        if not self._active:
            return
        radius = min(width, height) / 2.0
        context.arc(width / 2.0, height / 2.0, radius, 0, 6.283185307)
        context.set_source_rgba(0.314, 0.961, 0.635, 1.0)
        context.fill()


class DpiPage(Gtk.Box):
    def __init__(self, workspace) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page")
        self.workspace = workspace
        self.presentation = ProfileDpiState(workspace)
        self._syncing = False
        self._stage_buttons: dict[int, Gtk.Button] = {}
        self._stage_scales: dict[int, Gtk.Scale] = {}
        self._stage_checks: dict[int, Gtk.CheckButton] = {}
        self._stage_value_labels: dict[int, Gtk.Label] = {}
        self._stage_active_lights: dict[int, ActiveStageLight] = {}
        self.presentation.subscribe(self._refresh)
        workspace.subscribe(self._workspace_changed)
        self.append(self._build_stages_card())
        self._refresh(self.presentation)

    def _workspace_changed(self, reason: str) -> None:
        if reason != "dpi":
            self.presentation.changed()

    def _build_stages_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card-panel")
        card.set_hexpand(True)
        card.set_vexpand(True)
        title = Gtk.Label(label="DPI Stages", xalign=0)
        title.add_css_class("panel-title")
        card.append(title)
        subtitle = Gtk.Label(
            label="Click a stage name to make it the start-up stage. DPI+ and DPI− cycle through enabled stages.",
            xalign=0, wrap=True,
        )
        subtitle.add_css_class("panel-subtitle")
        card.append(subtitle)
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=DPI_STAGE_GAP)
        columns.add_css_class("dpi-stage-columns")
        for number in range(1, 6):
            columns.append(self._build_stage_card(number))
        stage_group = Gtk.CenterBox()
        stage_group.add_css_class("dpi-stage-group")
        stage_group.set_hexpand(True)
        stage_group.set_vexpand(True)
        stage_group.set_margin_top(DPI_STAGE_TOP_MARGIN)
        stage_group.set_margin_start(DPI_STAGE_SIDE_PADDING)
        stage_group.set_margin_end(DPI_STAGE_SIDE_PADDING)
        stage_group.set_margin_bottom(DPI_STAGE_BOTTOM_MARGIN)
        stage_group.set_center_widget(columns)
        card.append(stage_group)
        self._stage_columns = columns
        card.append(self._build_bottom_row())
        return card

    def _build_stage_card(self, number: int) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.add_css_class("dpi-stage-card")
        card.set_size_request(DPI_STAGE_WIDTH, -1)
        card.set_hexpand(False)
        button = Gtk.Button()
        button.add_css_class("dpi-stage-selector")
        button.set_size_request(DPI_STAGE_WIDTH, 32)
        button.set_can_focus(True)
        apply_accessible_label(button, f"Make DPI stage {number} the start-up stage")
        button.connect("clicked", self._stage_selected, number)
        header_content = Gtk.Overlay()
        header_label = Gtk.Label(label=f"DPI {number}")
        header_label.set_halign(Gtk.Align.CENTER)
        header_content.set_child(header_label)
        active = ActiveStageLight()
        active.add_css_class("dpi-active-light")
        header_content.add_overlay(active)
        button.set_child(header_content)
        card.append(button)
        self._stage_buttons[number] = button
        self._stage_active_lights[number] = active
        check = Gtk.CheckButton(label="Enabled")
        check.set_can_focus(True)
        check.set_tooltip_text(f"Include DPI stage {number} in the DPI cycle")
        apply_accessible_label(check, f"DPI stage {number} enabled")
        check.connect("toggled", self._stage_enabled, number)
        card.append(check)
        self._stage_checks[number] = check
        value = Gtk.Label(xalign=.5)
        value.add_css_class("dpi-stage-value")
        card.append(value)
        self._stage_value_labels[number] = value
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, DPI_RAW_MIN, DPI_RAW_MAX, 1)
        scale.set_inverted(True)
        scale.set_draw_value(False)
        scale.set_vexpand(True)
        scale.set_size_request(36, DPI_SLIDER_HEIGHT)
        scale.set_round_digits(0)
        scale.set_tooltip_text("Drag or use the arrow keys to change DPI")
        apply_accessible_label(scale, f"DPI value for stage {number}")
        scale.connect("value-changed", self._stage_scale_changed, number)
        card.append(scale)
        self._stage_scales[number] = scale
        card.append(Gtk.Label(label="200–16,400", xalign=.5, css_classes=["dpi-range-label"]))
        return card

    def _build_bottom_row(self) -> Gtk.Box:
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.add_css_class("dpi-bottom-row")
        self._message = Gtk.Label(xalign=0, wrap=True)
        self._message.add_css_class("dpi-message")
        self._message.set_hexpand(True)
        bottom.append(self._message)
        defaults = Gtk.Button(label="Defaults")
        defaults.add_css_class("action-button")
        defaults.add_css_class("profile-action-button")   # same size as the bottom-bar buttons
        defaults.set_valign(Gtk.Align.CENTER)
        tip = "Set all five stages to 1000, 2000, 4000, 8200 and 16400 DPI (all on); click Apply to send"
        defaults.set_tooltip_text(tip)
        apply_accessible_label(defaults, tip)
        defaults.connect("clicked", lambda _button: self.presentation.restore_defaults())
        self._defaults_button = defaults
        bottom.append(defaults)
        return bottom

    # ------------------------------------------------------------ handlers
    def _show_rule(self, message: str) -> None:
        self._message.set_text(message)
        self._message.add_css_class("warning-text")

    def _stage_selected(self, _button, number: int) -> None:
        try:
            self.presentation.set_default_stage(number)
        except DpiRuleError as exc:
            self._show_rule(str(exc))

    def _stage_enabled(self, check, number: int) -> None:
        if self._syncing:
            return
        try:
            self.presentation.set_enabled(number, check.get_active())
        except DpiRuleError as exc:
            self._show_rule(str(exc))
            self._refresh(self.presentation, keep_message=True)

    def _stage_scale_changed(self, scale, number: int) -> None:
        if self._syncing:
            return
        raw = round(scale.get_value())
        if dpi_raw_to_dpi(raw) is not None:
            self.presentation.set_dpi_raw(number, raw)

    # ------------------------------------------------------------ refresh
    def _refresh(self, state: ProfileDpiState, keep_message: bool = False) -> None:
        self._syncing = True
        try:
            stages = state.stages
            default = state.default_stage
            for stage in stages:
                self._refresh_stage(stage, default)
        finally:
            self._syncing = False
        if not keep_message:
            self._message.remove_css_class("warning-text")
            if default == 0:
                self._message.set_text(
                    f"The stored start-up stage value ({state.profile.selected_dpi_stage_raw}) is not "
                    "one of the five stages; choose a stage to replace it."
                )
            else:
                stage = stages[default - 1]
                self._message.set_text(
                    f"Profile {self.workspace.selected_slot} starts at DPI {default} ({stage.display} DPI)."
                )

    def _refresh_stage(self, stage: StageView, default: int) -> None:
        number = stage.number
        self._stage_value_labels[number].set_text(stage.display)
        is_default = number == default
        self._stage_active_lights[number].set_active(is_default)
        button = self._stage_buttons[number]
        button.set_tooltip_text(
            "Start-up stage" if is_default else f"Make DPI {number} the start-up stage"
        )
        if is_default:
            button.add_css_class("selected-dpi-stage")
        else:
            button.remove_css_class("selected-dpi-stage")
        set_accessible_selected(button, is_default)
        check = self._stage_checks[number]
        check.set_active(bool(stage.enabled))
        check.set_inconsistent(stage.enabled is None)
        scale = self._stage_scales[number]
        scale.set_value(stage.slider_raw)
        scale.set_sensitive(stage.enabled is not False)
