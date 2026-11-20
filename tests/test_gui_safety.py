from pathlib import Path

from venus_profile.gui.safety import safety_audit


def test_gui_safety_audit():
    assert len(safety_audit()) == 4


def test_gui_source_has_no_invalid_display_namespace_or_live_surface():
    root = Path(__file__).parents[1] / "src" / "venus_profile" / "gui"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "Gtk.Display" not in source
    assert "DeviceSession" not in source
    assert "/dev/hidraw" not in source
    assert "/dev/bus/usb" not in source
    assert "discover_venus_hidraw" not in source
    # The legacy dark-theme hint may only ever be *cleared* (to quiet a
    # libadwaita warning caused by desktop settings), never requested, and
    # Gtk.Settings may only be touched in prepare_gtk_environment().
    assert source.count("gtk-application-prefer-dark-theme") == 3  # docstring + get + clear
    assert 'settings.set_property("gtk-application-prefer-dark-theme", False)' in source
    assert 'set_property("gtk-application-prefer-dark-theme", True)' not in source
    assert "prefer_dark_theme" not in source
    assert source.count("Gtk.Settings") == 1
    application = (root / "application.py").read_text(encoding="utf-8")
    body = application[application.index("def prepare_gtk_environment"):application.index("def main(")]
    assert "Gtk.Settings.get_default()" in body
    assert "Adw.StyleManager.get_default().set_color_scheme" in source
    assert "set_accessible_label" not in source
