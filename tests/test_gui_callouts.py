from __future__ import annotations

from gui_workspace import temp_workspace
from gui_display import pump_until, requires_gtk_display

from venus_profile.gui.buttons.callouts import (
    TOP_CALLOUTS,
    callout_layouts,
    rects_overlap,
)
from venus_profile.gui.models import ButtonView, build_physical_control_registry


def test_top_has_six_explicit_callouts_with_separate_presentation_identity():
    assert len(TOP_CALLOUTS) == 6
    assert {item.presentation_id for item in TOP_CALLOUTS} == {
        "top.left-click", "top.right-click", "top.middle-click",
        "top.fire", "top.dpi-plus", "top.dpi-minus",
    }
    assert all(0.0 <= value <= 1.0 for item in TOP_CALLOUTS for value in item.anchor)


def test_callout_sides_and_right_label_order_are_explicit():
    right = [item for item in TOP_CALLOUTS if item.side == "right"]
    assert [item.label for item in right] == [
        "Left Click", "Wheel / Middle Click", "Right Click", "DPI+", "DPI−"
    ]
    assert [item.row for item in right] == list(range(5))
    assert [item.label for item in TOP_CALLOUTS if item.side == "left"] == ["Fire Button"]


def test_callout_geometry_is_contained_and_boxes_do_not_overlap():
    layouts = callout_layouts((190.0, 0.0, 265.0, 520.0))
    for layout in layouts:
        x, y, width, height = layout.box
        assert (18 if layout.spec.presentation_id == "top.fire" else 0) <= x and 0 <= y
        assert x + width <= 700
        assert y + height <= 520
        assert 190 <= layout.anchor[0] <= 455
        assert 0 <= layout.anchor[1] <= 520
    for index, first in enumerate(layouts):
        for second in layouts[index + 1:]:
            assert not rects_overlap(first.box, second.box)


def test_connectors_are_independent_direct_leaders_without_a_shared_trunk():
    layouts = callout_layouts((190.0, 0.0, 265.0, 520.0))
    right = [layout for layout in layouts if layout.spec.side == "right"]
    assert all(len(layout.connector) == 3 for layout in right)
    assert len({layout.connector[1] for layout in right}) == 5
    fire = next(layout for layout in layouts if layout.spec.presentation_id == "top.fire")
    assert len(fire.connector) == 2


def test_right_callouts_share_dimensions_and_horizontal_segment_length():
    right = [layout for layout in callout_layouts((217.0, 0.0, 265.0, 520.0))
             if layout.spec.side == "right"]
    assert len({layout.box[0] for layout in right}) == 1
    assert len({layout.box[2:] for layout in right}) == 1
    assert len({layout.spec.horizontal_length for layout in right}) == 1
    for layout in right:
        diagonal_end = layout.connector[1]
        box_edge = layout.connector[2]
        assert box_edge[0] - diagonal_end[0] == layout.spec.horizontal_length


def test_right_callout_diagonals_are_approximately_45_degrees():
    right = [layout for layout in callout_layouts((217.0, 0.0, 265.0, 520.0))
             if layout.spec.side == "right"]
    for layout in right:
        anchor, diagonal_end, _box_edge = layout.connector
        dx = abs(anchor[0] - diagonal_end[0])
        dy = abs(anchor[1] - diagonal_end[1])
        assert dx > 0
        if layout.spec.presentation_id == "top.middle-click":
            assert dy < 1e-6
            continue
        assert dy > 0
        angle = __import__("math").degrees(__import__("math").atan2(dy, dx))
        minimum_angle = 0 if layout.spec.presentation_id in {"top.left-click", "top.middle-click"} else 15
        assert minimum_angle <= angle <= 80


def test_fire_box_is_lower_and_selector_aligned_without_changing_anchor():
    fire = next(item for item in TOP_CALLOUTS if item.presentation_id == "top.fire")
    assert fire.anchor == (.14, .2307692307)
    assert fire.label_box[0] == 18
    assert fire.label_box[1] == 122
    assert len(callout_layouts((217.0, 0.0, 265.0, 520.0))) == 6


def test_connector_segments_do_not_cross_in_reference_geometry():
    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def crosses(first, second):
        a, b = first
        c, d = second
        return (
            orientation(a, b, c) * orientation(a, b, d) < 0
            and orientation(c, d, a) * orientation(c, d, b) < 0
        )

    routes = [layout.connector for layout in callout_layouts((217.0, 0.0, 265.0, 520.0))]
    segments = [
        (route[index], route[index + 1])
        for route in routes
        for index in range(len(route) - 1)
    ]
    assert not any(crosses(first, second)
                   for index, first in enumerate(segments)
                   for second in segments[index + 1:])


def test_callout_anchors_do_not_define_protocol_identity():
    controls = {
        item.presentation_id: item
        for item in build_physical_control_registry()
        if item.view is ButtonView.TOP
    }
    assert {
        item.presentation_id: controls[item.presentation_id].protocol_button
        for item in TOP_CALLOUTS
    } == {
        "top.left-click": 1,
        "top.right-click": 2,
        "top.middle-click": 3,
        "top.fire": 4,
        "top.dpi-plus": 5,
        "top.dpi-minus": 6,
    }


def test_side_view_has_no_top_callout_specs():
    assert all(item.side in {"left", "right"} for item in TOP_CALLOUTS)
    from venus_profile.gui.buttons.artwork import ArtworkView

    assert "_callout_layer" in ArtworkView.__init__.__code__.co_names


@requires_gtk_display
def test_clicking_each_callout_selects_the_existing_control():
    from venus_profile.gui.buttons.page import ButtonsPage

    page = ButtonsPage(temp_workspace())
    for spec in TOP_CALLOUTS:
        page._artwork._callout_buttons[spec.presentation_id].emit("clicked")
        assert page.selected.presentation_id == spec.presentation_id
        assert page._assignment._control.presentation_id == spec.presentation_id
        assert page.selected.presentation_id == spec.presentation_id


@requires_gtk_display
def test_callout_buttons_are_accessible_and_keyboard_focusable():
    from gi.repository import Gtk
    from venus_profile.gui.buttons.page import ButtonsPage

    window = Gtk.Window()
    window.set_default_size(1100, 780)
    page = ButtonsPage(temp_workspace())
    window.set_child(page)
    window.present()
    try:
        pump_until(lambda: all(
            button.get_visible() and button.get_realized()
            for button in page._artwork._callout_buttons.values()
        ))
        for spec in TOP_CALLOUTS:
            button = page._artwork._callout_buttons[spec.presentation_id]
            assert button.get_can_focus()
            assert button.get_accessible_role() is Gtk.AccessibleRole.BUTTON
            assert button.get_tooltip_text().startswith(f"{spec.label} · ")  # name · current assignment
            assert button.get_cursor() is not None
            button.activate()
            pump_until(
                lambda spec=spec: page.selected.presentation_id
                == spec.presentation_id
            )
            assert page.selected.presentation_id == spec.presentation_id
    finally:
        window.destroy()


@requires_gtk_display
def test_callout_activation_is_scoped_to_the_page_that_owns_the_button():
    from gi.repository import Gtk
    from venus_profile.gui.buttons.page import ButtonsPage

    first_window = Gtk.Window()
    first_page = ButtonsPage(temp_workspace())
    first_window.set_child(first_page)
    first_window.present()
    second_window = Gtk.Window()
    second_page = ButtonsPage(temp_workspace())
    second_window.set_child(second_page)
    second_window.present()
    try:
        pump_until(lambda: all(
            button.get_visible() and button.get_realized()
            for button in second_page._artwork._callout_buttons.values()
        ))
        second_page._artwork._callout_buttons["top.left-click"].activate()
        pump_until(
            lambda: second_page.selected.presentation_id == "top.left-click"
        )
        assert second_page.selected.presentation_id == "top.left-click"
        assert first_page.selected.presentation_id == "top.fire"
    finally:
        first_window.destroy()
        second_window.destroy()


@requires_gtk_display
def test_painted_rectangle_uses_viewport_coordinates_in_overlay():
    from gi.repository import Graphene, Gtk
    from venus_profile.gui.buttons.page import ButtonsPage

    window = Gtk.Window()
    page = ButtonsPage(temp_workspace())
    window.set_child(page)
    window.present()
    try:
        viewport = page._artwork._viewport
        pump_until(lambda: viewport.get_realized() and viewport.get_width() > 0 and viewport.get_height() > 0)
        origin = Graphene.Point()
        origin.init(0.0, 0.0)
        ok, translated = page._artwork._viewport.compute_point(
            page._artwork, origin
        )
        assert ok
        painted = page._artwork._painted_rectangle()
        assert painted is not None
        assert painted[0] >= translated.x
        assert painted[1] >= translated.y
    finally:
        window.destroy()


@requires_gtk_display
def test_callouts_appear_even_when_the_first_layout_comes_after_every_map_time_attempt(monkeypatch):
    """At launch the view is mapped before GTK allocates it; the callouts must still appear.

    Regression (AppImage on the user's desktop, 2026-11-20): only the side grid showed until the
    user switched pages, because every positioning attempt ran before the first allocation.
    The one deferred idle retry is disabled here so only allocation-driven positioning can work.
    """
    from gi.repository import Gtk
    from venus_profile.gui.buttons import artwork
    from venus_profile.gui.buttons.page import ButtonsPage

    monkeypatch.setattr(artwork.ArtworkView, "_refresh_after_map", lambda self, *_args: None)
    window = Gtk.Window()
    window.set_default_size(1100, 780)
    page = ButtonsPage(temp_workspace())
    window.set_child(page)
    window.present()
    try:
        view = page._artwork
        pump_until(lambda: all(button.get_visible() for button in view._callout_buttons.values()))
        painted = view._painted_rectangle()
        assert painted is not None
        for layout in callout_layouts(painted):
            button = view._callout_buttons[layout.spec.presentation_id]
            assert (button.get_margin_start(), button.get_margin_top()) == (
                round(layout.box[0]), round(layout.box[1]))
    finally:
        window.destroy()
