"""Regression tests for the deterministic GTK display gate."""

from gui_display import (
    GTK_DISPLAY,
    GTK_DISPLAY_AVAILABLE,
    GTK_INITIALIZED,
    pump_until,
    requires_gtk_display,
)

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


def test_display_gate_requires_initialisation_and_a_default_display():
    assert GTK_DISPLAY_AVAILABLE is (
        GTK_INITIALIZED and GTK_DISPLAY is not None
    )
    assert GTK_DISPLAY is Gdk.Display.get_default()


@requires_gtk_display
def test_available_gate_allows_safe_window_construction_and_activation():
    window = Gtk.Window()
    button = Gtk.Button(label="gate probe")
    window.set_child(button)
    window.present()
    try:
        fired = []
        button.connect("clicked", lambda *_widget: fired.append(True))
        pump_until(lambda: window.get_realized() and button.get_realized())
        button.activate()
        pump_until(lambda: bool(fired))
        assert fired, "keyboard activation did not dispatch clicked"
    finally:
        window.destroy()
