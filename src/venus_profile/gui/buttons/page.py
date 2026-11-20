"""Unified offline Buttons page."""

from __future__ import annotations

from math import hypot

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ... import button_functions as bf
from ..assignment.panel import AssignmentPanel
from ..models import ButtonView, PhysicalControl, build_physical_control_registry
from .artwork import ArtworkView, TOP_CALLOUT_CANVAS_WIDTH, TOP_POLICY
from .side_grid import SideButtonGrid

SIDE_GRID_MARGIN_START = 18
SIDE_GRID_MARGIN_BOTTOM = 32


class SideGroupConnector(Gtk.DrawingArea):
    """One decorative leader from the mouse side-button strip to the grid."""

    def __init__(self) -> None:
        super().__init__()
        self._selected = False
        self.set_can_target(False)
        self.set_focusable(False)
        self.set_draw_func(self._draw)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.queue_draw()

    def _draw(self, _area, context, width, height) -> None:
        if width <= 0 or height <= 0:
            return
        context.set_source_rgb(.18, .59, 1.0)
        context.set_line_width(2.0 if self._selected else 1.0)
        anchor = (width * .345 + 10, height * .665 - 50)
        target = (width * .165 + 2, height * .775 - 8)
        dx = anchor[0] - target[0]
        dy = anchor[1] - target[1]
        length = hypot(dx, dy)
        if length > 50:
            target = (target[0] + dx * 50 / length + 35, target[1] + dy * 50 / length)
        context.move_to(*anchor)
        context.line_to(*target)
        context.stroke()
        context.arc(anchor[0], anchor[1], 3.5 if self._selected else 3.0, 0, 6.2832)
        context.fill()


class ButtonsPage(Gtk.Box):
    def __init__(self, workspace) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page")
        self.workspace = workspace
        self._controls = build_physical_control_registry()
        self._top_controls = tuple(item for item in self._controls if item.view is ButtonView.TOP)
        self._side_controls = tuple(item for item in self._controls if item.view is ButtonView.SIDE)
        self.selected = next(item for item in self._controls if item.protocol_button == 4)
        workspace.subscribe(self._workspace_changed)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.append(body)
        self._body = body

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left.add_css_class("card-panel")
        title = Gtk.Label(label="Mouse controls", xalign=0)
        title.add_css_class("panel-title")
        left.append(title)
        subtitle = Gtk.Label(label="Select a physical control", xalign=0)
        subtitle.add_css_class("panel-subtitle")
        left.append(subtitle)

        artwork_region = Gtk.Overlay()
        artwork_region.add_css_class("artwork-region")
        artwork_region.add_css_class("artwork-stage")
        artwork_region.set_size_request(TOP_CALLOUT_CANVAS_WIDTH, TOP_POLICY.max_height)
        artwork_region.set_hexpand(True)
        artwork_region.set_vexpand(True)
        self._artwork = ArtworkView(self._top_controls, self.select_control)
        artwork_region.set_child(self._artwork)
        self._connector = SideGroupConnector()
        artwork_region.add_overlay(self._connector)
        self._side_grid = SideButtonGrid(self._side_controls, self.select_control)
        self._side_grid.set_halign(Gtk.Align.START)
        self._side_grid.set_valign(Gtk.Align.END)
        self._side_grid.set_margin_start(SIDE_GRID_MARGIN_START)
        self._side_grid.set_margin_bottom(SIDE_GRID_MARGIN_BOTTOM)
        artwork_region.add_overlay(self._side_grid)
        self._visual_area = artwork_region
        left.append(artwork_region)

        hint = Gtk.Label(
            label="Hover a control to see its assignment. Wheel up/down keep their stored functions.",
            xalign=0, wrap=True,
        )
        hint.add_css_class("dim-label")
        hint.add_css_class("buttons-hint")
        left.append(hint)
        body.append(left)

        self._assignment = AssignmentPanel(workspace, self.selected)
        self._assignment.set_size_request(414, -1)
        body.append(self._assignment)

        self.select_control(self.selected)
        self._refresh_tooltips()

    def _workspace_changed(self, _reason: str) -> None:
        self._assignment.refresh()
        self._refresh_tooltips()

    def _refresh_tooltips(self) -> None:
        profile = self.workspace.profile()
        for control in self._controls:
            label = bf.read_assignment(profile, control.protocol_button).label
            text = f"{control.display_label} · {label}"
            self._artwork.set_assignment_tooltip(control.presentation_id, text)
            self._side_grid.set_assignment_tooltip(control.presentation_id, text)

    def select_control(self, control: PhysicalControl) -> None:
        if control not in self._controls:
            raise ValueError("cannot select a control outside the offline registry")
        self.selected = control
        self._connector.set_selected(control.view is ButtonView.SIDE)
        self._artwork.select(control)
        self._side_grid.select(control)
        self._assignment.update_control(control)
