"""Focused, display-gated tests for provisional staging-row drag reordering."""

from pathlib import Path

import pytest

from macro_workspace import demo_workspace

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

from gui_display import pump_until, requires_gtk_display


def _pump() -> None:
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)


def _open_page():
    from venus_profile.gui.macros.page import MacrosPage

    window = Gtk.Window()
    window.set_default_size(1100, 780)
    page = MacrosPage(demo_workspace())
    window.set_child(page)
    window.present()
    _pump()
    poe = next(item for item in page.presentation.local_macros if item.name == "POE-Flasks")
    page.presentation.select_macro(poe.stable_id)
    _pump()
    return window, page


def _order(page):
    return tuple(row._macro_action_id for row in page._action_row_children())


def _attach_lifted(page):
    state = page._action_drag
    lifted = page._build_lifted_action_preview(state.row)
    state.lifted = lifted
    page._lifted_layer.add_overlay(lifted)
    page._place_lifted_over_row(state)
    _pump()
    # Allocation happens on the next frame-clock tick, not as a pending event.
    # The staging list is padded and has a classic scrollbar, so rows are
    # narrower than the scroller; the lifted copy must match the row exactly.
    pump_until(lambda: lifted.get_allocated_width() == state.row.get_allocated_width() > 0)
    root = page.get_root()
    ok_row, row_point = state.row.compute_point(root, Graphene.Point())
    ok_lifted, lifted_point = lifted.compute_point(root, Graphene.Point())
    assert ok_row and ok_lifted
    assert abs(row_point.x - lifted_point.x) < 1
    assert abs(row_point.y - lifted_point.y) < 1  # sits exactly on the source row
    return lifted


@requires_gtk_display
def test_provisional_reorder_is_view_only_and_cancel_restores_exact_order():
    window, page = _open_page()
    try:
        ids = page.presentation.selected_macro.current_action_ids
        records = page.presentation.selected_macro.current_records
        assert page._begin_action_reorder(ids[2])
        page._move_dragged_row_to(0)
        assert _order(page) != ids
        assert page.presentation.selected_macro.current_action_ids == ids
        assert page.presentation.selected_macro.current_records == records
        page._finish_action_reorder_drag(commit=False)
        assert _order(page) == ids
        assert page.presentation.selected_macro.current_action_ids == ids
    finally:
        window.close()


@requires_gtk_display
def test_commit_mutates_model_once_and_same_position_is_noop():
    window, page = _open_page()
    try:
        ids = page.presentation.selected_macro.current_action_ids
        notices = []
        page.presentation.subscribe(lambda state: notices.append(state.selected_macro.current_action_ids))
        assert page._begin_action_reorder(ids[2])
        page._move_dragged_row_to(0)
        assert page._commit_action_reorder()
        assert len(notices) == 1
        assert page.presentation.selected_macro.current_action_ids[0] == ids[2]
        notices.clear()
        current = page.presentation.selected_macro.current_action_ids
        assert page._begin_action_reorder(current[0])
        assert page._commit_action_reorder()
        assert notices == []
    finally:
        window.close()


@requires_gtk_display
def test_midpoints_container_drop_and_foreign_payload_rejection():
    window, page = _open_page()
    try:
        ids = page.presentation.selected_macro.current_action_ids
        assert page._action_list_drop_target.get_widget() is page._actions_box
        assert page._begin_action_reorder(ids[0])
        others = [row for row in page._action_row_children() if row is not page._action_drag.row]
        first = others[0].get_allocation()
        assert page._compute_target_index(first.y + first.height / 2 - 1) == 0
        assert page._compute_target_index(first.y + first.height / 2 + 1) == 1
        assert not page._action_list_drag_drop(None, "local-macro:foreign", 4, 4)
        assert not page._action_list_drag_drop(None, "device-macro-slot:0", 4, 4)
        assert page._action_drag is not None
        page._move_dragged_row_to(2)
        assert page._action_list_drag_drop(None, f"action-id:{ids[0]}", 4, 4)
    finally:
        window.close()


@requires_gtk_display
def test_autoscroll_moves_only_near_viewport_edges():
    window, page = _open_page()
    try:
        adjustment = page._sequence_scroll.get_vadjustment()
        # The staging viewport fills the card height, so make sure the list
        # genuinely overflows it before exercising edge autoscroll.
        from venus_profile.actions import ActionRecord
        from venus_profile.macro import ACTION_RECORD_CAPACITY

        while (adjustment.get_upper() <= adjustment.get_page_size() + 40
               and page.presentation.selected_macro.current_action_count < ACTION_RECORD_CAPACITY):
            page.presentation.add_action(ActionRecord.action_delay_ms(10))
            _pump()
        assert adjustment.get_upper() > adjustment.get_page_size()
        ids = page.presentation.selected_macro.current_action_ids
        assert page._begin_action_reorder(ids[0])
        viewport = adjustment.get_page_size()
        adjustment.set_value((adjustment.get_upper() - viewport) / 2)
        page._action_drag.pointer_viewport_y = 5
        before = adjustment.get_value()
        page._action_autoscroll_tick()
        assert adjustment.get_value() < before
        page._action_drag.pointer_viewport_y = viewport / 2
        before = adjustment.get_value()
        page._action_autoscroll_tick()
        assert adjustment.get_value() == before
        page._finish_action_reorder_drag(commit=False)
    finally:
        window.close()


@requires_gtk_display
def test_lifted_allocation_matches_source_and_full_overlay_layer():
    window, page = _open_page()
    try:
        ids = page.presentation.selected_macro.current_action_ids
        assert page._begin_action_reorder(ids[1])
        state = page._action_drag
        lifted = _attach_lifted(page)
        source = state.row.get_allocation()
        replica = lifted.get_allocation()
        layer = page._lifted_layer.get_allocation()
        overlay = page._sequence_overlay.get_allocation()
        scroll = page._sequence_scroll.get_allocation()
        # The lifted coordinate plane must be the overlay viewport, not a
        # natural-size child centred or clipped by its parent Gtk.Overlay.
        assert layer.width == overlay.width == scroll.width
        assert layer.height == overlay.height == scroll.height
        # The widget carrying the lifted background and border is `lifted`
        # itself, so this compares final painted allocations, not requests.
        assert replica.width == source.width
        assert lifted.get_halign() is Gtk.Align.FILL
        page._finish_action_reorder_drag(commit=False)
        assert lifted.get_parent() is None
    finally:
        window.close()


@requires_gtk_display
def test_lifted_motion_is_vertical_only_and_native_icon_is_payload_free():
    window, page = _open_page()
    try:
        ids = page.presentation.selected_macro.current_action_ids
        assert page._begin_action_reorder(ids[1])
        state = page._action_drag
        lifted = _attach_lifted(page)
        row = state.row.get_allocation()
        page._capture_action_grab_offset(state, row.x + 20, row.y + 10)
        first = page._place_lifted_at_pointer(state, row.x + 20, 80)
        sideways = page._place_lifted_at_pointer(state, row.x + 300, 80)
        assert sideways == pytest.approx(first)
        vertical = page._place_lifted_at_pointer(state, row.x - 300, 100)
        assert vertical[0] == pytest.approx(first[0])
        assert vertical[1] > first[1]
        hidden = page._build_hidden_drag_icon()
        assert hidden.get_size_request() == (0, 0)
        assert hidden.get_first_child() is None
        page._finish_action_reorder_drag(commit=False)
        assert lifted.get_parent() is None
    finally:
        window.close()


def test_drag_css_is_narrow_and_payload_neutral():
    css = (Path(__file__).parents[1] / "src/venus_profile/gui/styles.css").read_text()
    assert ".macro-action-row.macro-action-row-lifted" in css
    assert ".macro-drag-icon-hidden" in css
    assert "action-id:" not in css
