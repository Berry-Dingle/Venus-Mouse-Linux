"""GTK4 accessibility value helpers for the installed PyGObject binding."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk


def _value(gtype, setter, value) -> GObject.Value:
    result = GObject.Value()
    result.init(gtype)
    getattr(result, setter)(value)
    return result


def accessible_string(value: str) -> GObject.Value:
    return _value(GObject.TYPE_STRING, "set_string", value)


def accessible_boolean(value: bool) -> GObject.Value:
    return _value(GObject.TYPE_BOOLEAN, "set_boolean", bool(value))


def accessible_state(value: bool) -> GObject.Value:
    """Gtk4 exposes selected state values as GValue integers in this runtime."""
    return _value(GObject.TYPE_INT, "set_int", int(bool(value)))


def apply_accessible_label(widget: Gtk.Widget, label: str) -> None:
    widget.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [accessible_string(label)],
    )


def set_accessible_selected(widget: Gtk.Widget, selected: bool) -> None:
    widget.update_state(
        [Gtk.AccessibleState.SELECTED],
        [accessible_state(selected)],
    )


_POINTER_WIDGET_TYPES = (
    Gtk.Button,
    Gtk.CheckButton,
    Gtk.Scale,
    Gtk.DropDown,
    Gtk.Expander,
)


def apply_pointer_cursor(widget: Gtk.Widget) -> None:
    """Use GTK's widget cursor API for interactive controls."""
    def refresh_cursor(control: Gtk.Widget, _param=None) -> None:
        control.set_cursor_from_name("pointer" if control.get_sensitive() else None)

    widget.connect("notify::sensitive", refresh_cursor)
    refresh_cursor(widget)


def apply_pointer_cursor_tree(root: Gtk.Widget) -> None:
    """Apply pointer cursors to all current interactive descendants."""
    child = root.get_first_child()
    while child is not None:
        if isinstance(child, _POINTER_WIDGET_TYPES):
            apply_pointer_cursor(child)
        apply_pointer_cursor_tree(child)
        child = child.get_next_sibling()
