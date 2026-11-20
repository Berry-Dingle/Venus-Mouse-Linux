import pytest
from pathlib import Path

from venus_profile.gui.application import NAV_ICON_NAMES
from venus_profile.gui.navigation import NavigationState, PAGE_NAMES


def test_navigation_has_one_authoritative_selection():
    navigation = NavigationState()
    for page in PAGE_NAMES:
        assert navigation.select(page) == page
        assert navigation.selected_page == page


def test_navigation_rejects_unknown_pages():
    with pytest.raises(ValueError):
        NavigationState().select("unknown")


def test_navigation_uses_verified_icons_or_intentional_text_only_fallback():
    assert NAV_ICON_NAMES == {
        "buttons": "input-mouse-symbolic",
        "dpi": "find-location-symbolic",
        "lighting": "display-brightness-symbolic",
        "macros": "view-grid-symbolic",
        "settings": "preferences-system-symbolic",
    }
    assert all(all(ord(character) < 128 for character in name)
               for name in NAV_ICON_NAMES.values())


def test_configured_navigation_icons_resolve_in_installed_adwaita_theme():
    icon_root = Path("/usr/share/icons/Adwaita")
    for icon_name in NAV_ICON_NAMES.values():
        assert any(icon_root.rglob(f"{icon_name}.svg"))


def test_interactive_controls_use_gtk_pointer_cursor_api():
    root = Path(__file__).parents[1] / "src" / "venus_profile" / "gui"
    accessibility = (root / "accessibility.py").read_text(encoding="utf-8")
    application = (root / "application.py").read_text(encoding="utf-8")
    assert 'set_cursor_from_name("pointer"' in accessibility
    assert 'set_cursor_from_name("pointer" if control.get_sensitive() else None)' in accessibility
    assert "apply_pointer_cursor_tree(root)" in application
