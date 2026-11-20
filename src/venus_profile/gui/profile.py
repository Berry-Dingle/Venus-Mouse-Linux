"""The application's single profile selector (five slots)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .accessibility import apply_accessible_label, set_accessible_selected


class ProfilePills(Gtk.Box):
    """Five profile pills shared by every page.

    A dot marks a slot edited since its profile was loaded (edits are kept
    automatically; Cancel discards them).
    """

    def __init__(self, workspace) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("profile-pills")
        self.workspace = workspace
        self._buttons: dict[int, Gtk.Button] = {}
        caption = Gtk.Label(label="Profile")
        caption.add_css_class("profile-caption")
        self.append(caption)
        for number in range(1, 6):
            button = Gtk.Button(label=str(number))
            button.add_css_class("profile-pill")
            button.set_can_focus(True)
            button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            button.connect("clicked", self._clicked, number)
            self._buttons[number] = button
            self.append(button)
        workspace.subscribe(lambda _reason: self.refresh())
        self.refresh()

    @property
    def selected_profile(self) -> int:
        return self.workspace.selected_slot

    def button(self, number: int) -> Gtk.Button:
        return self._buttons[number]

    def _clicked(self, _button: Gtk.Button, number: int) -> None:
        self.workspace.select_slot(number)
        self.workspace.remember_selection()

    def refresh(self) -> None:
        edited = set(self.workspace.edited_slots())
        selected = self.workspace.selected_slot
        for number, button in self._buttons.items():
            active = number == selected
            if active:
                button.add_css_class("active-profile-pill")
            else:
                button.remove_css_class("active-profile-pill")
            dirty = number in edited
            button.set_label(f"{number}•" if dirty else str(number))
            if dirty:
                button.add_css_class("unsaved-profile-pill")
            else:
                button.remove_css_class("unsaved-profile-pill")
            name = self.workspace.loaded_name(number)
            state = f"{name}, edited" if dirty else name
            apply_accessible_label(button, f"Profile {number}, {state}")
            button.set_tooltip_text(f"Profile {number} · {state}")
            set_accessible_selected(button, active)
