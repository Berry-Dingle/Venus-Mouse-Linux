"""Deterministic GTK display gate and main-context helpers for GUI tests."""

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

GTK_INITIALIZED = bool(Gtk.init_check())
GTK_DISPLAY = Gdk.Display.get_default() if GTK_INITIALIZED else None
GTK_DISPLAY_AVAILABLE = GTK_INITIALIZED and GTK_DISPLAY is not None

requires_gtk_display = pytest.mark.skipif(
    not GTK_DISPLAY_AVAILABLE,
    reason="GTK display is unavailable",
)


def pump_until(condition, timeout_ms: int = 2000) -> None:
    """Iterate GTK's main context until condition succeeds or times out."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_ms / 1000
    wakeup = GLib.timeout_add(10, lambda: GLib.SOURCE_CONTINUE)
    try:
        while time.monotonic() < deadline:
            context.iteration(True)
            if condition():
                return
    finally:
        GLib.source_remove(wakeup)
    raise AssertionError(
        "GTK state did not reach the expected value before the deadline"
    )
