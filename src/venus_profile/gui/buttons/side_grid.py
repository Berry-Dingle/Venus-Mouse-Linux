"""Unified-screen presentation for the twelve physical side controls."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..accessibility import apply_accessible_label, set_accessible_selected
from ..models import PhysicalControl


class SideButtonGrid(Gtk.Grid):
    """A compact 4-column by 3-row physical side-button selector."""

    def __init__(self, controls: tuple[PhysicalControl, ...], on_selected) -> None:
        super().__init__(column_spacing=6, row_spacing=6)
        self.add_css_class("side-grid-card")
        self._controls = controls
        self._buttons: dict[str, Gtk.Button] = {}
        for control in controls:
            number = control.display_label.removeprefix("Thumb Button ")
            button = Gtk.Button(label=number)
            button.add_css_class("side-grid-cell")
            button.set_can_focus(True)
            button.set_cursor_from_name("pointer")
            button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            apply_accessible_label(button, control.description)
            button.set_tooltip_text(control.description)
            button.connect("clicked", lambda _button, item=control: on_selected(item))
            self._buttons[control.presentation_id] = button
            self.attach(button, control.grid_column, control.grid_row, 1, 1)

    def set_assignment_tooltip(self, presentation_id: str, text: str) -> None:
        button = self._buttons.get(presentation_id)
        if button is not None:
            button.set_tooltip_text(text)

    def select(self, selected: PhysicalControl) -> None:
        for control in self._controls:
            button = self._buttons[control.presentation_id]
            is_selected = control.presentation_id == selected.presentation_id
            if is_selected:
                button.add_css_class("selected-grid-cell")
            else:
                button.remove_css_class("selected-grid-cell")
            set_accessible_selected(button, is_selected)

