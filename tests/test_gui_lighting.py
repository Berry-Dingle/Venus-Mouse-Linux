from pathlib import Path

import pytest

from venus_profile.app_store import AppStore
from venus_profile.gui.lighting.model import MODES, ProfileLightingState, official_swatches
from venus_profile.gui.lighting.page import (
    PickerInteractionModel,
    _picker_debug,
    adjust_ring_colour,
)
from venus_profile.workspace import VenusWorkspace

from gui_display import requires_gtk_display


@pytest.mark.parametrize(
    ("colour", "maximum_delta"),
    [
        ((0, 255, 0), 20),
        ((255, 0, 0), 30),
        ((255, 255, 0), 20),
        ((255, 255, 255), 0),
    ],
)
def test_bright_ring_colours_receive_little_lightening(colour, maximum_delta):
    adjusted = adjust_ring_colour(colour)
    assert all(0 <= channel <= 255 for channel in adjusted)
    assert max(adjusted[index] - colour[index] for index in range(3)) <= maximum_delta


@pytest.mark.parametrize("colour", [(58, 15, 112), (16, 32, 96), (0, 48, 32), (2, 2, 2)])
def test_dark_ring_colours_are_lightened_without_mutating_source(colour):
    original = tuple(colour)
    adjusted = adjust_ring_colour(colour)
    assert adjusted != colour
    assert all(0 <= channel <= 255 for channel in adjusted)
    assert tuple(colour) == original


def test_dark_ring_adjustment_uses_the_lighter_final_blend_cap():
    assert max(adjust_ring_colour((1, 1, 1))) >= 120


def test_ring_adjustment_preserves_colour_direction_for_dark_purple():
    adjusted = adjust_ring_colour((58, 15, 112))
    assert adjusted[2] > adjusted[0] > adjusted[1]


def test_ring_uses_original_glow_colour_and_adaptive_stroke():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    preview = page[page.index("class PreviewHighlights"):page.index("class LightingPage")]
    assert "r, g, b = (channel / 255 for channel in self.state.colour)" in preview
    assert "adjust_ring_colour(" in preview
    assert "context.set_source(glow)" in preview


def test_mouse_backdrop_uses_one_smooth_stronger_gradient():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    backdrop = page[page.index("class PreviewBackdrop"):page.index("class PreviewHighlights")]
    assert "cairo.RadialGradient" in backdrop
    assert "glow.add_color_stop_rgba(0.0, r, g, b, .14)" in backdrop
    assert "for index in range(12, 0, -1)" not in backdrop


def _picker_model(hue=0.4, saturation=0.5, value=0.6):
    model = PickerInteractionModel((0, 0, 0))
    model.hue = hue
    model.saturation = saturation
    model.value = value
    return model


def test_sv_drag_leaving_below_preserves_horizontal_position():
    model = _picker_model()
    result = model.update_from_sv_drag(75, 40, 0, 100, 100, 100)
    assert model.saturation == pytest.approx(0.75)
    assert model.value == 0.0
    assert result.current_x == 75
    assert result.clamped_x == 75
    assert result.clamped_y == 100


def test_sv_drag_only_reaches_zero_saturation_after_leaving_left():
    model = _picker_model()
    model.update_from_sv_drag(75, 40, 0, 100, 100, 100)
    assert model.saturation == pytest.approx(0.75)
    model.update_from_sv_drag(75, 40, -100, 100, 100, 100)
    assert model.saturation == 0.0


@pytest.mark.parametrize(
    ("offset_x", "offset_y", "expected_saturation", "expected_value"),
    [
        (100, 0, 1.0, 0.6),
        (-100, 0, 0.0, 0.6),
        (0, -100, 0.75, 1.0),
    ],
)
def test_sv_drag_clamps_one_axis_and_preserves_the_other(
    offset_x, offset_y, expected_saturation, expected_value
):
    model = _picker_model()
    model.update_from_sv_drag(75, 40, offset_x, offset_y, 100, 100)
    assert model.saturation == pytest.approx(expected_saturation)
    assert model.value == pytest.approx(expected_value)


def test_sv_drag_cannot_change_hue():
    model = _picker_model(hue=0.42)
    model.update_from_sv_drag(75, 40, 10, 80, 100, 100)
    assert model.hue == pytest.approx(0.42)


def test_hue_drag_clamps_edges_without_changing_saturation_or_value():
    model = _picker_model(saturation=0.37, value=0.64)
    model.update_from_hue_drag(25, 100, 100, 20)
    assert model.hue == 1.0
    assert model.saturation == pytest.approx(0.37)
    assert model.value == pytest.approx(0.64)
    model.update_from_hue_drag(25, -100, 100, 20)
    assert model.hue == 0.0
    assert model.saturation == pytest.approx(0.37)
    assert model.value == pytest.approx(0.64)


def test_picker_markers_derive_only_from_normalized_model_values():
    model = _picker_model(hue=0.8, saturation=0.75, value=0.25)
    assert model.sv_marker(200, 100) == pytest.approx((150, 75))
    assert model.hue_marker(200) == pytest.approx(160)


def test_picker_debug_logging_is_gated_and_does_not_mutate_model(monkeypatch, capsys):
    model = _picker_model(hue=0.42, saturation=0.75, value=0.25)
    calculation = model.update_from_sv_drag(75, 40, 0, 100, 100, 100)
    values = (model.hue, model.saturation, model.value)
    monkeypatch.delenv("VENUS_PICKER_DEBUG", raising=False)
    _picker_debug("SV", "update", model, calculation)
    assert "VENUS_PICKER_DEBUG" not in capsys.readouterr().err
    monkeypatch.setenv("VENUS_PICKER_DEBUG", "1")
    _picker_debug("SV", "update", model, calculation)
    assert "VENUS_PICKER_DEBUG gesture=SV event=update" in capsys.readouterr().err
    assert values == (model.hue, model.saturation, model.value)


def test_picker_has_two_local_exclusive_gestures_and_no_translation_coordinator():
    page = (
        Path(__file__).parents[1]
        / "src"
        / "venus_profile"
        / "gui"
        / "lighting"
        / "page.py"
    ).read_text(encoding="utf-8")
    assert page.count("Gtk.GestureDrag()") == 2
    assert page.count("drag.set_button(Gdk.BUTTON_PRIMARY)") == 2
    assert page.count("drag.set_exclusive(True)") == 2
    assert page.count("Gtk.EventSequenceState.CLAIMED") == 2
    assert "attach_drag_surface" not in page
    assert "translate_coordinates" not in page
    assert "compute_point" not in page
    assert "compute_bounds" not in page
    assert "EventControllerMotion" not in page
    assert "enter-notify" not in page
    assert "leave-notify" not in page
    assert page.count('connect("unrealize", self._cancel_drag)') == 2


def test_lighting_mode_is_one_container_before_brightness_and_colour_controls():
    page = (
        Path(__file__).parents[1]
        / "src"
        / "venus_profile"
        / "gui"
        / "lighting"
        / "page.py"
    ).read_text(encoding="utf-8")
    assert 'mode_section.add_css_class("lighting-mode-container")' in page
    assert 'Gtk.Label(label="Lighting Mode"' in page
    assert 'self._mode_dropdown.add_css_class("lighting-dropdown")' not in page
    assert page.index("card.append(mode_section)") < page.index("card.append(level_controls)")
    assert page.index("card.append(level_controls)") < page.index("card.append(colour_controls_row)")


def test_lighting_preview_uses_requested_heading_without_old_subtitle():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    assert 'Gtk.Label(label="Lighting Preview", xalign=0)' in page
    assert 'label="Presentation-only offline fixture"' not in page


def test_lighting_controls_uses_requested_heading_without_old_subtitle():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    assert 'Gtk.Label(label="Lighting Controls", xalign=0)' in page
    assert 'label="Customize the offline lighting presentation"' not in page
    assert "scale:disabled marks label" in styles
    assert "color: #ffffff" in styles


def test_navigation_is_a_flat_segmented_control():
    styles = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    track = styles[styles.index(".navigation-row {"):styles.index(".navigation-row button {")]
    assert "background: @card-deep" in track
    assert "border: 1px solid @border-soft" in track
    assert "border-radius: 6px" in track
    navigation = styles[styles.index(".navigation-row {"):styles.index(".demo-badge")]
    # No glossy "jelly" treatment: no gradients, no bottom accent bar.
    assert "gradient" not in navigation
    assert "inset 0 -3px" not in navigation
    active = navigation[navigation.index(".navigation-row button.active-tab {"):]
    assert "background-color: @card-raised" in active
    assert ".navigation-divider" not in styles
    assert "scale:disabled {" in styles
    assert "opacity: 1" in styles


def test_preview_summary_label_measures_independently_of_colour_text():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    assert "self._preview_value.set_width_chars(PREVIEW_VALUE_WIDTH_CHARS)" in page
    assert "self._preview_value.set_max_width_chars(PREVIEW_VALUE_WIDTH_CHARS)" in page
    assert "self._preview_value.set_wrap(False)" in page
    assert "self._preview_value.set_ellipsize(Pango.EllipsizeMode.END)" in page


def test_picker_input_gap_is_added_without_changing_picker_column_height():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    styles = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    assert "self.set_size_request(-1, 130)" in page
    assert 'current.add_css_class("lighting-picker-current")' in page
    assert ".lighting-picker-current" in styles
    assert "margin-top: 5px" in styles


def test_hue_strip_grows_without_changing_picker_total_height():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    assert "self.set_size_request(-1, 35)" in page
    assert "gap.set_size_request(-1, 12)" in page
    assert "self.set_size_request(-1, 130)" in page


def test_wheel_highlight_reduces_only_from_the_bottom():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    assert "wheel_y = height * .235 - 34" in page
    assert "wheel_height = height * .155 - 10" in page


def test_lighting_preview_uses_rear_circle_instead_of_side_button_overlay():
    page = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    preview = page[page.index("class PreviewHighlights"):page.index("class LightingPage")]
    assert "side_x" not in preview
    assert "side_y" not in preview
    assert "circle_x = width * .50 + 10" in preview
    assert "circle_y = height * .78 + 25" in preview
    assert "circle_radius = width * .045" in preview
    assert "cairo.RadialGradient" in preview
    assert "glow.add_color_stop_rgba(1.0, r, g, b, 0.0)" in preview
    assert "context.set_source_rgba(ring_r, ring_g, ring_b, .18)" in preview
    assert "context.set_line_width(1)" in preview


def test_selected_swatch_uses_one_internal_accent_border():
    styles = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    selected = styles[styles.index(".lighting-swatch.selected-lighting-swatch"):styles.index(".swatch-red")]
    base = styles[styles.index(".lighting-swatch {"):styles.index(".lighting-swatch:hover")]
    assert "border: 2px solid rgba(255,255,255,.20)" in base
    assert "border: 2px solid @blue" in selected
    assert "box-shadow: inset 0 0 0 2px #ffffff" in selected


def test_brightness_scale_uses_confirmed_discrete_labels_in_order():
    model = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting" / "page.py").read_text(encoding="utf-8")
    from venus_profile.gui.lighting.model import BRIGHTNESS_LEVELS
    assert tuple((int(level), label) for level, label in BRIGHTNESS_LEVELS) == (
        (0, "Off"), (1, "Low"), (2, "Medium"), (3, "High"))
    assert "self._brightness.set_digits(0)" in model
    assert "self._brightness.add_mark(level, Gtk.PositionType.BOTTOM, label)" in model
    assert 'self._brightness_value.set_text' not in model
    assert "if scale.get_value() != level:" in model
    assert "scale.set_value(level)" in model
    assert 'brightness = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)' in model


@pytest.fixture
def workspace(tmp_path):
    return VenusWorkspace.open(AppStore(tmp_path / "store"))


def test_modes_map_to_pfd_values_and_colour_relevance():
    assert [(item.label, int(item.pfd_value)) for item in MODES] == [
        ("Full Brightness", 1), ("Breathing", 0), ("Wave", 3), ("Flash", 4), ("Rainbow", 2), ("Off", 5),
    ]
    assert [item.stable_id for item in MODES if not item.uses_colour] == ["rainbow", "off"]
    assert [item.stable_id for item in MODES if item.supports_breathing_speed] == ["breathing"]


def test_palette_is_the_33_entry_windows_grid():
    swatches = official_swatches()
    assert len(swatches) == 33
    assert swatches[0].colorref == 65721 and swatches[0].name == "Red"
    assert swatches[12].colorref == 3454210 and swatches[23].colorref == 16715534
    assert len({item.stable_id for item in swatches}) == 33


def test_state_reads_real_factory_lighting(workspace):
    state = ProfileLightingState(workspace)
    assert state.mode.stable_id == "breathing"
    assert state.colour_hex == "#FF0000"
    assert state.brightness_level == 2 and state.breathing_speed == 5
    workspace.select_slot(2)
    assert state.colour_hex == "#0000FF"


def test_edits_are_profile_specific_and_no_ops_stay_clean(workspace):
    state = ProfileLightingState(workspace)
    state.select_mode("breathing")
    state.set_brightness(2)
    state.select_colour((255, 0, 0))
    assert not workspace.dirty
    workspace.select_slot(4)
    state.select_mode("wave")
    state.select_swatch("palette-12")
    state.set_brightness(3)
    profile = workspace.profile(4)
    assert (profile.led_mode_raw, profile.led_color_raw, profile.brightness_raw) == (3, 3454210, 3)
    assert workspace.unsaved_profile_slots() == (4,)
    assert workspace.profile(1).led_mode_raw == 0


def test_custom_colour_uses_correct_colorref_and_preview_dims(workspace):
    state = ProfileLightingState(workspace)
    state.select_colour((0x12, 0x34, 0x56))
    assert workspace.profile(1).led_color_raw == 0x563412
    state.set_brightness(0)
    assert state.colour == (0, 0, 0)
    state.set_brightness(3)
    state.select_mode("off")
    assert state.colour == (0, 0, 0) and not state.colour_relevant


def test_unknown_mode_is_preserved_until_replaced(workspace):
    profile = workspace.profile(1)
    profile.document.set(profile.group_section, "LedMode1", "9")
    state = ProfileLightingState(workspace)
    assert state.mode is None and state.colour_relevant
    assert b"LedMode1=9" in profile.to_bytes()


@requires_gtk_display
def test_page_reflects_profile_and_refresh_never_edits(workspace):
    from venus_profile.gui.lighting.page import LightingPage
    workspace.profile(3).set_led_mode(2)
    workspace.save_all()
    page = LightingPage(workspace)
    for slot in (3, 1, 3, 2):
        workspace.select_slot(slot)
    assert not workspace.dirty
    workspace.select_slot(3)
    assert page._mode_dropdown.get_selected() == [m.stable_id for m in MODES].index("rainbow")
    assert page._swatch_section.get_sensitive() is False
    workspace.select_slot(1)
    assert page._swatch_section.get_sensitive() is True
    assert page._speed.get_sensitive() is True
    page._swatch_buttons["palette-23"].emit("clicked")
    assert workspace.profile(1).led_color_raw == 16715534
    assert "selected-lighting-swatch" in page._swatch_buttons["palette-23"].get_css_classes()
    page._colour_value.set_text("#00ff7f")
    page._colour_value.emit("activate")
    assert workspace.profile(1).led_color_hex == "#00FF7F"
    page._brightness.set_value(1)
    assert workspace.profile(1).brightness_raw == 1


def test_lighting_page_has_no_device_surface_or_dead_apply():
    root = Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "lighting"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert all(token not in source for token in ("DeviceSession", "hidraw", "discover_venus", "/dev/"))
    assert "self._apply" not in source
    assert "build_lighting_fixture" not in source and "Demo lighting" not in source


@requires_gtk_display
def test_card_allocations_do_not_follow_hex_text_while_picking(workspace):
    # Regression: the horizontal page box measures children width-for-height.
    # Measuring only at for_size=-1 missed the jitter, so check the real
    # allocations of a shown page and the label's width at its actual height.
    import time

    from gi.repository import GLib, Gtk

    from venus_profile.gui.application import apply_css
    from venus_profile.gui.lighting.page import LightingPage

    apply_css()
    page = LightingPage(workspace)
    window = Gtk.Window()
    window.set_default_size(1160, 700)
    window.set_child(page)
    window.present()

    def pump(seconds=0.12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)

    try:
        pump(0.4)
        label = page._preview_value
        preview_card = label.get_parent()
        controls_card = page._picker_section.get_parent()
        while "card-panel" not in controls_card.get_css_classes():
            controls_card = controls_card.get_parent()
        observed = set()
        # Hex strings with very different glyph widths (#111111 vs #DDDDDD).
        for rgb in ((0x11, 0x11, 0x11), (0xDD, 0xDD, 0xDD), (0xF3, 0xF1, 0xF1),
                    (0x34, 0x2E, 0x2E), (0xAB, 0xCD, 0xEF), (0x4D, 0x7E, 0x00)):
            page.presentation.select_colour(rgb)
            pump()
            assert page.presentation.colour_hex in label.get_text()
            height = label.get_allocation().height
            observed.add((
                label.measure(Gtk.Orientation.HORIZONTAL, height)[:2],
                preview_card.get_allocation().width,
                controls_card.get_allocation().width,
                window.get_width(),
            ))
        assert len(observed) == 1, sorted(observed)
    finally:
        window.close()
        pump(0.1)


def test_palette_adds_three_pink_red_swatches_for_a_6x6_grid():
    from venus_profile.gui.lighting.model import palette_swatches

    swatches = palette_swatches()
    assert len(swatches) == 36
    assert swatches[:33] == official_swatches()  # the Windows palette, unchanged
    assert [s.hex_value for s in swatches[33:]] == ["#E40A63", "#EE0748", "#F6042C"]
    assert all(not s.official for s in swatches[33:])
    assert len({s.stable_id for s in swatches}) == 36


def test_extra_swatch_is_stored_as_an_exact_custom_colour(workspace):
    state = ProfileLightingState(workspace)
    state.select_swatch("extra-01")
    assert state.colour_hex == "#EE0748"
    assert workspace.profile().led_color_hex == "#EE0748"
    state.select_swatch("palette-00")  # an official swatch still uses the palette value
    assert workspace.profile().led_color_raw == official_swatches()[0].colorref


@requires_gtk_display
def test_lighting_page_shows_6x6_grid_and_highlights_extra_swatch(workspace):
    from venus_profile.gui.lighting.page import LightingPage

    page = LightingPage(workspace)
    grid = page._swatch_buttons["palette-00"].get_parent()
    positions = {grid.query_child(page._swatch_buttons[key])[:2] for key in page._swatch_buttons}
    assert len(page._swatch_buttons) == 36
    assert {column for column, _row in positions} == set(range(6))
    assert {row for _column, row in positions} == set(range(6))
    page._swatch_buttons["extra-02"].emit("clicked")
    assert "selected-lighting-swatch" in page._swatch_buttons["extra-02"].get_css_classes()
    assert sum("selected-lighting-swatch" in b.get_css_classes() for b in page._swatch_buttons.values()) == 1
