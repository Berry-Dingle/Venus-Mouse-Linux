from __future__ import annotations

import pytest


gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from gui_display import requires_gtk_display
from gui_workspace import new_window, temp_workspace


@requires_gtk_display
def test_buttons_page_constructs_with_accessible_hotspots():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    assert page.selected.protocol_button == 4
    assert page.selected.view.value == "top"
    assert page._visual_area.get_child() is page._artwork
    assert len(page._artwork._callout_buttons) == 6
    assert len(page._side_grid._buttons) == 12
    assert not hasattr(page, "_bottom_selector")
    assert not hasattr(page, "_profile_pills")  # one global selector lives in the header
    assert not hasattr(page._assignment, "_assign")  # changes apply immediately
    assert page._assignment._current.get_text() == "Fire Key · Left button · 0 ms · 3 shots"


@requires_gtk_display
def test_unified_side_grid_has_physical_four_by_three_numbering():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    grid = page._side_grid
    assert [[grid.get_child_at(column, row).get_label()
              for column in range(4)] for row in range(3)] == [
        ["3", "6", "9", "12"],
        ["2", "5", "8", "11"],
        ["1", "4", "7", "10"],
    ]
    assert len(grid._buttons) == 12


@requires_gtk_display
def test_unified_side_grid_selection_updates_authoritative_state():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    for number in range(1, 13):
        control = next(item for item in page._side_controls
                       if item.display_label == f"Thumb Button {number}")
        page._side_grid._buttons[control.presentation_id].emit("clicked")
        assert page.selected is control
        assert page.selected.protocol_button == number + 6
        assert page._assignment._control is control
        assert page.selected is control


@requires_gtk_display
def test_unified_buttons_page_has_no_view_switcher():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    assert not hasattr(page, "_view_buttons")
    assert not hasattr(page, "_set_view")


@requires_gtk_display
def test_selection_updates_assignment_without_redundant_control_selector():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    dpi_minus = next(item for item in page._controls if item.protocol_button == 6)
    page.select_control(dpi_minus)
    assert page.selected is dpi_minus
    assert page._assignment._control is dpi_minus
    assert not hasattr(page, "_bottom_selector")


@requires_gtk_display
def test_profile_pills_are_one_click_global_selection():
    from venus_profile.gui.profile import ProfilePills
    workspace = temp_workspace()
    pills = ProfilePills(workspace)
    assert [pills.button(n).get_label() for n in range(1, 6)] == ["1", "2", "3", "4", "5"]
    for number in range(1, 6):
        pills.button(number).emit("clicked")
        assert workspace.selected_slot == number
        assert sum("active-profile-pill" in pills.button(n).get_css_classes() for n in range(1, 6)) == 1


@requires_gtk_display
def test_profile_pills_mark_slots_edited_since_load():
    from venus_profile.gui.profile import ProfilePills
    workspace = temp_workspace()
    pills = ProfilePills(workspace)
    workspace.profile(4).set_pointer_speed(3)
    workspace.edited()
    assert pills.button(4).get_label() == "4•"
    assert "unsaved-profile-pill" in pills.button(4).get_css_classes()
    assert pills.button(4).get_tooltip_text() == "Profile 4 · Factory profile, edited"
    workspace.save_all()  # persisting the working set keeps the "edited" mark
    assert pills.button(4).get_label() == "4•"
    workspace.cancel_edits(4)
    assert pills.button(4).get_label() == "4"


def test_fixed_window_and_artwork_policies_are_compact_and_distinct():
    from venus_profile.gui.application import OUTER_MARGIN, WINDOW_HEIGHT, WINDOW_WIDTH
    from venus_profile.gui.buttons.artwork import TOP_ARTWORK_MAX_HEIGHT, TOP_ARTWORK_MAX_WIDTH

    assert (WINDOW_WIDTH, WINDOW_HEIGHT) == (1100, 780)
    assert OUTER_MARGIN == 13
    assert WINDOW_WIDTH < 1500 and WINDOW_HEIGHT < 1000
    assert TOP_ARTWORK_MAX_HEIGHT == 520
    assert TOP_ARTWORK_MAX_WIDTH == 320


def test_buttons_page_has_no_breakpoint_container():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "page.py"
    ).read_text(encoding="utf-8")
    assert "BreakpointBin" not in source
    assert "install_responsive_breakpoint" not in source


def test_artwork_and_selector_use_centered_compact_policies():
    from pathlib import Path

    artwork_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "artwork.py"
    ).read_text(encoding="utf-8")
    page_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "page.py"
    ).read_text(encoding="utf-8")
    assert "class TextureViewport(Gtk.Widget)" in artwork_source
    assert "snapshot.append_texture" in artwork_source
    assert "segmented-selector" not in page_source
    assert "_view_buttons" not in page_source
    assert "SideGroupConnector" in page_source
    assert "SideButtonGrid" in page_source
    assert "artwork_region.add_overlay(self._side_grid)" in page_source
    assert "self.painted_rectangle" in artwork_source
    assert "SIDE_ARTWORK" not in artwork_source
    assert "side-hotspot" not in artwork_source


def test_artwork_geometry_uses_gtk4_coordinate_translation():
    from pathlib import Path

    source = (
        Path(__file__).parents[1]
        / "src/venus_profile/gui/buttons/artwork.py"
    ).read_text(encoding="utf-8")
    method = source[source.index("    def _painted_rectangle("):]
    assert "compute_point(self, origin)" in method
    assert "get_allocation()" not in method


def test_application_css_loads_as_a_gtk_4_12_string():
    from venus_profile.gui.application import _css

    provider = Gtk.CssProvider()
    provider.load_from_string(_css())
    assert provider.to_string()


def test_outer_margin_and_side_container_are_shared_layout_rules():
    from pathlib import Path

    application_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "application.py"
    ).read_text(encoding="utf-8")
    page_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "page.py"
    ).read_text(encoding="utf-8")
    assert application_source.count("OUTER_MARGIN") >= 4
    assert "artwork_region.set_hexpand(True)" in page_source
    assert "artwork_region.set_vexpand(True)" in page_source


def test_unified_top_policy_has_no_side_runtime_policy():
    from venus_profile.gui.buttons.artwork import TOP_POLICY

    assert TOP_POLICY.css_class == "artwork-top"
    assert TOP_POLICY.max_width == 320
    assert TOP_POLICY.max_height == 520


def test_side_grid_uses_one_uniform_cell_style_and_one_group_connector():
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    page_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "page.py"
    ).read_text(encoding="utf-8")
    assert ".side-grid-cell {" in styles
    assert "border-radius: 6px;" in styles
    assert "side-hotspot-button" not in styles
    assert page_source.count("SideGroupConnector()") == 1
    assert "for control in controls" not in page_source


@requires_gtk_display
def test_accessible_selected_update_emits_no_glib_type_warning(capsys):
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    page._artwork.select(page.selected)
    captured = capsys.readouterr()
    assert "g_value_get_int" not in captured.err
    assert "G_VALUE_HOLDS_INT" not in captured.err


def test_accessibility_helpers_create_typed_values():
    from gi.repository import GObject
    from venus_profile.gui.accessibility import accessible_boolean, accessible_state, accessible_string

    label = accessible_string("Buttons")
    selected = accessible_boolean(True)
    state = accessible_state(True)
    assert label.g_type == GObject.TYPE_STRING
    assert label.get_string() == "Buttons"
    assert selected.g_type == GObject.TYPE_BOOLEAN
    assert selected.get_boolean() is True
    assert state.g_type == GObject.TYPE_INT
    assert state.get_int() == 1


@requires_gtk_display
def test_window_and_navigation_construct_with_accessible_labels():
    window = new_window()
    assert len(window._nav_buttons) == 5
    assert all(button.get_accessible_role() == Gtk.AccessibleRole.BUTTON
               for button in window._nav_buttons.values())
    assert [button.get_child().get_last_child().get_text()
            for button in window._nav_buttons.values()] == [
                "Buttons", "Lighting", "Macros", "DPI", "Settings"
            ]
    assert window._stack.get_visible_child_name() == "buttons"
    assert sum("active-tab" in button.get_css_classes()
               for button in window._nav_buttons.values()) == 1
    window._nav_buttons["lighting"].emit("clicked")
    assert window._stack.get_visible_child_name() == "lighting"
    assert sum("active-tab" in button.get_css_classes()
               for button in window._nav_buttons.values()) == 1
    window._allow_close = True
    window.close()


def test_shell_hierarchy_has_navigation_row_and_global_profile_selector():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "application.py"
    ).read_text(encoding="utf-8")
    assert "header.set_title_widget(nav)" in source
    assert "bar.append(self._profile_pills)" in source
    assert "navigation-row" in source
    assert 'Gtk.Button(label="Save")' not in source
    assert "profile-action-bar" in source
    assert "Gtk.MenuButton(" in source
    assert "Open PFD" not in source and "Save As…" not in source


def test_every_page_is_bound_to_the_workspace():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "application.py"
    ).read_text(encoding="utf-8")
    for page in ("ButtonsPage", "DpiPage", "LightingPage", "SettingsPage"):
        assert f"{page}(self.workspace)" in source
    assert "MacrosPage(self.workspace, window=self)" in source
    assert "_placeholder" not in source


@requires_gtk_display
def test_all_navigation_icons_resolve_in_active_icon_theme():
    from venus_profile.gui.application import NAV_ICON_NAMES
    window = new_window()
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    assert all(theme.has_icon(icon_name) for icon_name in NAV_ICON_NAMES.values())
    window._allow_close = True
    window.close()


@requires_gtk_display
def test_switching_all_pages_keeps_fixed_shell_dimensions():
    window = new_window()
    window.present()
    while GLib.MainContext.default().iteration(False):
        pass
    expected = (1100, 780)
    assert tuple(window.get_default_size()) == expected
    for page in ("buttons", "dpi", "lighting", "macros", "settings"):
        window._select_page(page)
        while GLib.MainContext.default().iteration(False):
            pass
        assert tuple(window.get_default_size()) == expected
    window._allow_close = True
    window.close()


@requires_gtk_display
def test_unified_page_contains_one_artwork_and_one_group_connector():
    from venus_profile.gui.buttons.page import ButtonsPage
    page = ButtonsPage(temp_workspace())
    assert page._visual_area.get_child() is page._artwork
    assert page._side_grid.get_parent() is page._visual_area
    assert page._artwork.get_next_sibling() is not None
    assert page._artwork.get_next_sibling().get_next_sibling() is page._side_grid


def test_cli_has_no_separate_demo_view_mode():
    from venus_profile.gui.application import main
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "application.py"
    ).read_text(encoding="utf-8")
    assert "--demo-view" not in source
    assert "DeviceSession" not in source
    assert main.__name__ == "main"


def test_gui_has_no_advanced_details_expanders():
    from pathlib import Path

    gui = Path(__file__).parents[1] / "src" / "venus_profile" / "gui"
    for source in gui.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "Advanced details" not in text, source
        assert "Gtk.Expander(" not in text, source
    styles = (gui / "styles.css").read_text(encoding="utf-8")
    for selector in ("expander {", ".advanced-text", ".lighting-advanced", ".dpi-advanced", ".macro-advanced"):
        assert selector not in styles


def test_no_stylesheet_border_radius_exceeds_six_pixels():
    import re
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    radii = [int(value) for value in re.findall(r"border-radius:\s*(\d+)px", styles)]
    assert radii
    assert max(radii) <= 6


def _css_rule(styles: str, selector: str) -> str:
    start = styles.index(selector)
    return styles[start:styles.index("}", start)]


def test_profile_pills_and_choice_toggles_are_flat():
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    pills = styles[styles.index(".profile-pill {"):styles.index("/* Main cards */")]
    assert "gradient" not in pills
    assert "inset" not in pills
    assert "background: @blue" in _css_rule(styles, ".profile-pill.active-profile-pill {")
    toggles = styles[styles.index(".polling-choice {"):styles.index(".device-status-badge")]
    assert "gradient" not in toggles
    assert "inset" not in toggles


def test_shared_controls_are_flat_with_six_pixel_radius():
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    fields = _css_rule(styles, "entry,\nspinbutton,\ndropdown > button {")
    assert "border-radius: 6px" in fields
    assert "box-shadow: none" in fields
    buttons = _css_rule(styles, "button.action-button {")
    assert "border-radius: 6px" in buttons
    assert "gradient" not in buttons
    assert "border-radius: 6px" in _css_rule(styles, "popover > contents {")
    assert "background: @blue" in _css_rule(styles, "button.action-button.suggested-action {")
    # Dropdowns are framed once, by the shared rule, not again by their card.
    assert ".section-card dropdown" not in styles


def test_every_labelled_action_button_uses_the_shared_class():
    import re
    from pathlib import Path

    gui = Path(__file__).parents[1] / "src" / "venus_profile" / "gui"
    sources = {
        "assignment/panel.py": ("toggle",),
        "settings.py": ("self._preview_button",),
        "macros/page.py": ("self._cancel_impact", "self._replace_impact", "self._record_button",
                           "self._stop_button", "self._revert_macro_button"),
    }
    for relative, names in sources.items():
        text = (gui / relative).read_text(encoding="utf-8")
        for name in names:
            assert re.search(rf'{re.escape(name)}\.add_css_class\("action-button"\)', text), (relative, name)
    application = (gui / "application.py").read_text(encoding="utf-8")
    assert 'button.add_css_class("action-button")\n            button.add_css_class("profile-action-button")' in application
    macros = (gui / "macros/page.py").read_text(encoding="utf-8")
    for helper_class in ("macro-library-button", "macro-action-button"):
        assert f'button.add_css_class("action-button")\n        button.add_css_class("{helper_class}")' in macros


@requires_gtk_display
def test_macro_action_column_does_not_grow_the_window():
    """Eleven stacked action buttons must not make Macros the tallest page."""
    from venus_profile.gui.application import apply_css

    apply_css()
    window = new_window()
    try:
        heights = {
            name: page.measure(Gtk.Orientation.VERTICAL, 1100)[0]
            for name, page in window.pages.items()
        }
        assert heights["macros"] <= max(height for name, height in heights.items() if name != "macros")
    finally:
        window.destroy()


@requires_gtk_display
def test_button_text_in_assignment_card_is_not_dimmed_by_card_captions():
    """`.section-card label` must not reach labels inside buttons/dropdowns."""
    from venus_profile.gui.application import apply_css

    apply_css()
    window = new_window()
    window.present()
    try:
        context = GLib.MainContext.default()
        for _ in range(200):
            context.iteration(False)
        panel = window.pages["buttons"]

        def walk(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from walk(child)
                child = child.get_next_sibling()

        colours = {}
        for widget in walk(panel):
            if isinstance(widget, Gtk.Label) and widget.get_mapped():
                button = widget.get_ancestor(Gtk.Button)
                if button is not None:
                    colour = widget.get_color()
                    colours[widget.get_text()] = (colour.red, colour.green, colour.blue)
        # Dropdown text in the assignment card (e.g. "Fire key") must use the
        # control's bright text, not the muted card-caption colour.
        muted = (0xA6 / 255, 0xB1 / 255, 0xBF / 255)
        for text in ("Fire key", "Left button"):
            assert min(colours[text]) > max(muted) + .1, (text, colours[text])
    finally:
        window.destroy()


def test_macro_library_rows_are_flat_with_a_slight_blue_border():
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    assert styles.count(".macro-library-row.selected-macro-row {") == 1
    base = _css_rule(styles, ".macro-library-row {")
    selected = _css_rule(styles, ".macro-library-row.selected-macro-row {")
    assert "border: 1px solid rgba(47, 151, 255, .38)" in base
    for rule in (base, selected):
        assert "gradient" not in rule
        assert "inset" not in rule
    assert "box-shadow: none" in selected


def test_renderer_default_uses_current_gl_name(monkeypatch):
    from venus_profile.gui.application import prepare_gtk_environment

    monkeypatch.delenv("GSK_RENDERER", raising=False)
    prepare_gtk_environment()
    import os
    assert os.environ["GSK_RENDERER"] == "gl"
    monkeypatch.setenv("GSK_RENDERER", "vulkan")
    prepare_gtk_environment()
    assert os.environ["GSK_RENDERER"] == "vulkan"  # user choice respected


def test_stylesheet_owns_the_libadwaita_palette_and_stock_widgets():
    """The app's CSS defines every palette variable stock widgets read."""
    from pathlib import Path

    styles = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "styles.css"
    ).read_text(encoding="utf-8")
    root = _css_rule(styles, ":root {")
    for variable in ("--window-bg-color", "--view-bg-color", "--headerbar-bg-color",
                     "--card-bg-color", "--dialog-bg-color", "--popover-bg-color",
                     "--accent-bg-color", "--accent-color", "--destructive-bg-color",
                     "--border-color", "--warning-color", "--error-color"):
        assert f"{variable}:" in root, variable
    for selector in ("tooltip,", "checkbutton check {", "scrollbar slider {", "windowcontrols button,",
                     "toast {", "dialog sheet,", "dialog.alert .response-area > button {",
                     "list.boxed-list,", "selection,"):
        assert selector in styles, selector
    for rule in ("toast {", "dialog sheet,", "tooltip,"):
        assert "border-radius: 6px" in _css_rule(styles, rule), rule



def test_app_icon_is_packaged_in_every_size_and_desktop_entry_uses_it():
    from pathlib import Path

    root = Path(__file__).parents[1]
    icon_id = "io.github.berry_dingle.VenusMouse"
    for base in (root / "packaging" / "icons", root / "src" / "venus_profile" / "gui" / "data" / "icons"):
        assert (base / "hicolor" / "scalable" / "apps" / f"{icon_id}.svg").exists()
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            assert (base / "hicolor" / f"{size}x{size}" / "apps" / f"{icon_id}.png").exists(), (base, size)
    desktop = (root / "packaging" / f"{icon_id}.desktop").read_text(encoding="utf-8")
    assert f"Icon={icon_id}" in desktop
    assert "Exec=venus-profile-gui" in desktop


@requires_gtk_display
def test_window_uses_the_packaged_app_icon():
    from gi.repository import Gdk
    from venus_profile.gui.application import APPLICATION_ID

    window = new_window()
    try:
        assert window.get_icon_name() == APPLICATION_ID
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        assert theme.has_icon(APPLICATION_ID)
    finally:
        window.destroy()


def test_header_drops_only_the_icon_and_menu_title_buttons():
    from venus_profile.gui.application import _without_icon_button

    assert _without_icon_button("icon:minimize,maximize,close") == ":minimize,maximize,close"
    assert _without_icon_button("menu:minimize,close") == ":minimize,close"
    assert _without_icon_button("close,minimize:icon") == "close,minimize:"
    assert _without_icon_button(None) == ":minimize,maximize,close"


def test_title_font_is_bundled_with_its_licence_and_loads():
    from pathlib import Path

    import gi
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo
    from venus_profile.gui.application import load_app_fonts

    fonts = Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "data" / "fonts"
    assert (fonts / "AlexBrush-Regular.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
    assert "SIL Open Font License" in (fonts / "AlexBrush-OFL.txt").read_text(encoding="utf-8")
    assert load_app_fonts() is True
    families = {family.get_name() for family in PangoCairo.FontMap.get_default().list_families()}
    assert "Alex Brush" in families
    styles = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    assert 'font-family: "Alex Brush"' in _css_rule(styles, ".app-title {")


@requires_gtk_display
def test_window_controls_are_as_tall_as_the_menu_button():
    """Minimise/close are centred like the menu button, not stretched to the header."""
    import time
    from venus_profile.gui.application import _descendants
    window = new_window()
    window.present()
    try:
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        controls = []
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            controls = [button for wc in _descendants(window, Gtk.WindowControls)
                        for button in _descendants(wc, Gtk.Button)]
            if controls and window._menu_button.get_first_child().get_height() > 0 \
                    and all(button.get_height() > 0 for button in controls):
                break
            time.sleep(0.02)
        assert controls                                          # minimise / close exist
        assert all(wc.get_valign() == Gtk.Align.CENTER for wc in _descendants(window, Gtk.WindowControls))
        menu = window._menu_button.get_first_child()
        assert {button.get_height() for button in controls} == {menu.get_height()}
    finally:
        window._allow_close = True
        window.close()
