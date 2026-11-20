from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from macro_workspace import demo_workspace

from venus_profile.actions import ActionRecord, ActionType
from venus_profile.gui.macros.model import (
    LOOP_LABELS,
    MacroPresentationState,
    format_action_display_value,
    format_action_editor_value,
    format_keyboard_display,
    format_keyboard_editor,
    parse_keyboard_editor,
    parse_keyboard_editor_value,
)


def test_local_library_is_not_limited_to_device_slot_count():
    state = MacroPresentationState(demo_workspace())
    assert len(state.local_macros) > 15
    for _ in range(10):
        state.add_local_macro("Extra")
    assert len(state.local_macros) > 25
    assert len({item.name for item in state.local_macros}) == len(state.local_macros)


def test_device_bank_has_exactly_fifteen_shared_slots():
    state = MacroPresentationState(demo_workspace())
    assert len(state.device_slots) == 15
    assert [item.stable_id for item in state.device_slots] == [
        f"device-macro-slot:{index}" for index in range(15)
    ]
    assert all(item.label == f"Slot {index + 1}" for index, item in enumerate(state.device_slots))


def test_slots_are_not_profile_banks_and_unresolved_slots_are_hidden():
    state = MacroPresentationState(demo_workspace())
    assert all("profile" not in item.stable_id for item in state.device_slots)
    assert not any(item.slot_index >= 15 for item in state.device_slots)


def test_fixture_name_prefixes_do_not_define_slot_identity():
    state = MacroPresentationState(demo_workspace())
    selected = next(item for item in state.local_macros if item.name == "15_Key1_LClick_Key2")
    assert selected.stable_id != "device-macro-slot:14"
    assert all(item.loaded_macro_name != "15_Key1_LClick_Key2" for item in state.device_slots)


def test_selecting_a_local_macro_updates_only_presentation_state():
    state = MacroPresentationState(demo_workspace())
    selected = next(item for item in state.local_macros if item.name == "11_Ctrl1")
    state.select_macro(selected.stable_id)
    assert state.selected_macro.name == "11_Ctrl1"
    assert state.populated_slot_count == 2


def test_known_actions_delays_and_summaries_are_fixture_backed():
    state = MacroPresentationState(demo_workspace())
    poe = next(item for item in state.local_macros if item.name == "POE-Flasks")
    assert poe.action_count == 19
    assert poe.encoded_delay_total_ms == 90
    assert poe.actions[0].action == "Keyboard press"
    assert poe.actions[0].input_label == "1"
    assert poe.actions[1].delay_label == "10 ms"
    assert poe.actions[-1].action == "Keyboard release"

    mouse = next(item for item in state.local_macros if item.name == "12_MouseLeftClick")
    assert mouse.actions[0].input_label == "Left button"
    assert mouse.actions[1].action == "Mouse release"
    assert mouse.warnings


def test_loop_metadata_uses_only_four_confirmed_modes():
    state = MacroPresentationState(demo_workspace())
    modes = {item.current_loop_type: item.current_loop_label for item in state.local_macros}
    assert modes == LOOP_LABELS


def test_shared_slot_usage_can_reference_multiple_profiles_and_buttons():
    state = MacroPresentationState(demo_workspace())
    slot = state.device_slots[0]
    assert slot.loaded_macro_name == "POE-Flasks"
    assert [(item.profile, item.button) for item in slot.usage] == [(1, 7)]
    assert state.device_slots[1].usage[0].button == 18


def test_loaded_macros_stay_visible_in_library_with_slot_badge():
    page_source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "if item.loaded_slot is not None:" in page_source
    assert 'label=f"S{item.loaded_slot + 1}"' in page_source
    state = MacroPresentationState(demo_workspace())
    loaded = next(item for item in state.local_macros if item.loaded_slot is not None)
    assert loaded.loaded_slot == 0


def test_gui_macro_modules_have_no_device_facing_surface():
    root = Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for forbidden in ("DeviceSession", "hidraw", "/dev/", "usb.core", "discover_", "subprocess"):
        assert forbidden not in source
    assert "/home/" not in source


def test_macros_page_has_no_device_apply_and_records_only_this_window():
    source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "self._apply =" not in source and "self._apply.set" not in source
    assert "Keyboard only · this window" in source
    assert "root.add_controller(controller)" in source
    assert "root.remove_controller(self._recording_controller)" in source


def test_macro_creator_uses_compact_settings_selected_editor_and_record_rows():
    source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "_build_creator_settings()" in source
    assert "_build_record_row()" in source
    assert "_begin_inline_action_edit" in source
    assert "_commit_inline_action" in source
    assert "_editor_summary" in source
    assert 'Create and Edit a Macro' in source
    assert 'label="Loop"' not in source
    assert "_build_selected_action_editor" not in source


def test_action_pointer_path_separates_drag_and_value_zones():
    source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "drag_source = Gtk.DragSource()" in source
    assert "drag_body.add_controller(drag_source)" in source
    assert "drag_body.add_controller(drag_selection)" in source
    assert "value_body.add_controller(value_gesture)" in source
    assert "def _action_drag_zone_event(" in source
    assert "def _action_value_pressed(" in source
    assert "if n_press == 2:" in source
    assert "ACTION_VALUE_ZONE_WIDTH = 118" in source
    assert "row.add_controller(source)" not in source[source.index("def _action_row"):source.index("def _gesture_log")]


def test_keyboard_and_mouse_usage_edits_are_model_backed_and_offline():
    state = MacroPresentationState(demo_workspace())
    state.select_action(0)
    state.edit_selected_usage(0x1E)
    assert state.selected_macro.current_records[0].usage == 0x1E
    mouse = next(item for item in state.local_macros if item.name == "12_MouseLeftClick")
    state.select_macro(mouse.stable_id)
    state.select_action(0)
    state.edit_selected_usage(0x02)
    assert state.selected_macro.current_records[0].usage == 0x02


def test_display_and_editor_value_formatters_are_separate():
    state = MacroPresentationState(demo_workspace())
    keyboard = state.selected_macro.current_records[0]
    delay = state.selected_macro.current_records[1]
    assert format_action_display_value(keyboard) == "1"
    assert format_action_editor_value(keyboard) == "1"
    assert parse_keyboard_editor_value("1") == 30
    assert parse_keyboard_editor_value("0x1f") == 31
    assert format_action_display_value(delay) == "10 ms"
    assert format_action_editor_value(delay) == "10"


def test_friendly_letters_digits_and_modifiers_round_trip():
    for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        usage = 0x04 + index
        assert parse_keyboard_editor(letter) == usage
        assert parse_keyboard_editor(letter.lower()) == usage
        assert format_keyboard_editor(usage) == letter
        assert format_keyboard_display(usage) == letter
    for index, digit in enumerate("1234567890"):
        usage = 0x1E + index
        assert parse_keyboard_editor(digit) == usage
        assert format_keyboard_editor(usage) == digit
        assert format_keyboard_display(usage) == digit
    for name, usage in (
        ("Left Ctrl", 0xE0),
        ("Left Shift", 0xE1),
        ("Space", 0x2C),
        ("Enter", 0x28),
    ):
        assert parse_keyboard_editor(name) == usage
        assert format_keyboard_display(usage) == name


def test_keyboard_parser_prefers_friendly_names_before_usage_fallback():
    assert parse_keyboard_editor("7") == 0x24
    assert parse_keyboard_editor("A") == 0x04
    assert parse_keyboard_editor("30") == 30
    assert parse_keyboard_editor("0x1E") == 30
    with pytest.raises(ValueError):
        parse_keyboard_editor("not a keyboard key")


def test_offline_editor_supports_bounded_in_memory_action_edits():
    state = MacroPresentationState(demo_workspace())
    state.select_action(0)
    original = state.selected_macro.current_action_count
    state.add_action(ActionRecord.action_delay_ms(20))
    assert state.selected_macro.current_action_count == original + 1
    assert state.selected_macro.current_records[-1].action_type == ActionType.DELAY
    state.edit_selected_delay(30)
    assert state.selected_macro.current_records[-1].delay_ms == 30
    state.copy_selected_action()
    state.paste_action()
    assert state.selected_macro.current_action_count == original + 2
    state.delete_selected_action()
    assert state.selected_macro.current_action_count == original + 1


def test_stable_action_edit_mutates_one_exact_record_with_one_notification():
    state = MacroPresentationState(demo_workspace())
    target_id = state.selected_macro.current_actions[2].stable_id
    untouched = state.selected_macro.current_records[:2]
    notifications = []
    state.subscribe(lambda current: notifications.append(current.selected_macro.current_records))
    state.edit_action_usage(target_id, 31)
    assert state.selected_macro.current_records[:2] == untouched
    assert state.selected_macro.current_records[2].usage == 31
    assert notifications == [state.selected_macro.current_records]


def test_stable_action_identity_survives_reorder_before_edit():
    state = MacroPresentationState(demo_workspace())
    target_id = state.selected_macro.current_actions[2].stable_id
    state.reorder_action(2, 0)
    state.edit_action_usage(target_id, 31)
    assert state.selected_macro.current_action_ids[0] == target_id
    assert state.selected_macro.current_records[0].usage == 31


def test_offline_editor_reorders_and_limits_records_without_touching_fixture():
    state = MacroPresentationState(demo_workspace())
    source = state.selected_macro.macro
    state.select_action(1)
    before = state.selected_macro.current_records
    state.move_selected_action(-1)
    assert state.selected_macro.current_records[0] == before[1]
    assert state.selected_macro.macro is source
    for _ in range(63):
        try:
            state.add_action(ActionRecord.keyboard(0x1E, True))
        except ValueError:
            break
    assert state.selected_macro.current_action_count <= 63


def test_loop_editing_keeps_only_the_four_confirmed_modes():
    state = MacroPresentationState(demo_workspace())
    state.edit_loop(3)
    assert state.selected_macro.current_loop_label == LOOP_LABELS[3]
    with pytest.raises(ValueError):
        state.edit_loop(4)


def test_slot_drop_and_swap_keep_library_and_button_references():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    macro = next(item for item in state.local_macros if item.name == "11_Ctrl1")
    state.drop_library_on_slot(macro.stable_id, 2)
    assert state.device_slots[2].loaded_macro_id == macro.stable_id
    impact = state.swap_slots(0, 2)                  # no confirmation: buttons follow their macros
    assert impact is None and state.pending_impact is None
    assert state.device_slots[2].loaded_macro_name == "POE-Flasks"
    assert state.device_slots[0].loaded_macro_name == "11_Ctrl1"
    # The button that ran POE-Flasks still runs it, now from slot 3.
    assert [(r.profile, r.button, r.slot, r.macro_name) for r in workspace.references_to_slot(2)] == [
        (1, 7, 2, "POE-Flasks")
    ]


def test_referenced_replacement_requires_explicit_confirmation_and_lists_usage():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    macro = next(item for item in state.local_macros if item.name == "11_Ctrl1")
    impact = state.drop_library_on_slot(macro.stable_id, 0)
    assert impact is not None
    assert impact.operation == "replace"
    assert "Profile 1 · Thumb 1" in impact.message
    assert state.device_slots[0].loaded_macro_name == "POE-Flasks"
    state.cancel_pending_impact()
    assert state.pending_impact is None
    state.drop_library_on_slot(macro.stable_id, 0)
    state.confirm_pending_impact()
    assert state.device_slots[0].loaded_macro_name == "11_Ctrl1"
    assert workspace.references_to_slot(0)[0].macro_name == "11_Ctrl1"


def test_referenced_unload_requires_confirmation_and_disables_buttons():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    impact = state.unload_slot(0)
    assert impact is not None
    assert "Profile 1 · Thumb 1" in impact.message
    state.confirm_pending_impact()
    assert state.device_slots[0].is_empty
    assert state.device_slots[0].usage == ()
    from venus_profile import button_functions as bf
    assert bf.read_assignment(workspace.profile(1), 7).function_code == bf.FUNCTION_DISABLE


def test_library_mutations_are_unsaved_until_save_and_then_persist():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    original = len(state.local_macros)
    new_id = state.add_local_macro("POE Flasks")
    assert len(state.local_macros) == original + 1
    assert state.dirty and state.selected_macro.dirty
    state.rename_selected_macro("POE Flasks Renamed")
    duplicate_id = state.duplicate_selected_macro()
    assert duplicate_id != new_id
    assert state.selected_macro.name == "POE Flasks Renamed Copy"
    workspace.save_all()
    assert not state.dirty
    from venus_profile.workspace import VenusWorkspace
    reopened = VenusWorkspace.open(workspace.store)
    names = {entry.name for entry in reopened.library.entries}
    assert {"POE Flasks Renamed", "POE Flasks Renamed Copy"} <= names


gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

from gui_display import pump_until, requires_gtk_display


def _pump_gtk() -> None:
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)


def _point_in_root(widget: Gtk.Widget, root: Gtk.Widget) -> tuple[float, float]:
    point = Graphene.Point()
    point.init(max(1, widget.get_width() / 2), max(1, widget.get_height() / 2))
    ok, translated = widget.compute_point(root, point)
    assert ok
    return translated.x, translated.y


def _open_windowed_macros_page():
    from venus_profile.gui.macros.page import MacrosPage

    window = Gtk.Window()
    window.set_default_size(1100, 780)
    page = MacrosPage(demo_workspace())
    window.set_child(page)
    window.present()
    _pump_gtk()
    assert page._click_away_root is window
    assert isinstance(page._click_away_controller, Gtk.EventControllerLegacy)
    assert page._click_away_controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
    return window, page


def _settled(widget: Gtk.Widget) -> bool:
    """True once the widget's allocation has reached its measured minimum."""
    minimum_width = widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    minimum_height = widget.measure(Gtk.Orientation.VERTICAL, -1)[0]
    return (widget.get_mapped() and widget.get_allocated_width() >= max(2, minimum_width)
            and widget.get_allocated_height() >= max(2, minimum_height))


def _capture_click(page, widget: Gtk.Widget) -> None:
    x, y = _point_in_root(widget, page._click_away_root)
    page._application_click_away_at(page._click_away_root, x, y)


@requires_gtk_display
def test_macros_page_constructs_visible_bank_and_creator_columns():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    assert len(page.presentation.local_macros) > 15
    assert len(page.presentation.device_slots) == 15
    assert len(page._library_rows) == len(page.presentation.local_macros)
    assert len(page._slot_rows) == 15
    assert page._record_button.get_sensitive() is True
    assert page._stop_button.get_sensitive() is False
    assert page._slot_list.get_parent() is not None
    assert page._actions_box.get_parent() is not None
    target = next(item for item in page.presentation.local_macros if item.name == "11_Ctrl1")
    page._select_macro(None, target.stable_id)
    assert page.presentation.selected_macro.name == "11_Ctrl1"
    assert page._editor_title.get_text() == "11_Ctrl1"


def _controllers(widget: Gtk.Widget) -> tuple[Gtk.EventController, ...]:
    model = widget.observe_controllers()
    return tuple(model.get_item(index) for index in range(model.get_n_items()))


def _label_texts(widget: Gtk.Widget) -> tuple[str, ...]:
    texts = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(_label_texts(child))
        child = child.get_next_sibling()
    return tuple(texts)


@requires_gtk_display
def test_action_rows_have_distinct_drag_and_value_controller_zones():
    window, page = _open_windowed_macros_page()
    try:
        for row in page._action_rows.values():
            assert row.get_first_child() is row._macro_drag_body
            assert row._macro_drag_body.get_next_sibling() is row._macro_value_body
            assert row._macro_value_body.get_next_sibling() is None
            assert row._macro_drag_source.get_widget() is row._macro_drag_body
            assert row._macro_value_click_gesture.get_widget() is row._macro_value_body
            assert any(
                isinstance(controller, Gtk.DragSource)
                for controller in _controllers(row._macro_drag_body)
            )
            assert any(
                isinstance(controller, Gtk.EventControllerLegacy)
                for controller in _controllers(row._macro_drag_body)
            )
            assert not any(
                isinstance(controller, Gtk.GestureClick)
                for controller in _controllers(row._macro_drag_body)
            )
            assert not any(
                isinstance(controller, Gtk.DragSource)
                for controller in _controllers(row._macro_value_body)
            )
            assert row._macro_inline_editor.get_parent() is row._macro_value_body
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_all_macro_drag_sources_keep_private_payloads_out_of_preview_text():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    action = page.presentation.selected_macro.current_actions[0]
    action_source = page._action_rows[action.stable_id]._macro_drag_source
    assert action_source._macro_drag_payload == f"action-id:{action.stable_id}"
    assert action_source._macro_drag_preview_title == "01  Keyboard press"
    assert action_source._macro_drag_preview_value == "1"

    library_id, library_row = next(iter(page._library_rows.items()))
    library_source = library_row._macro_drag_source
    library_item = next(
        item for item in page.presentation.local_macros
        if item.stable_id == library_id
    )
    assert library_source._macro_drag_payload == library_id
    assert library_source._macro_drag_preview_title == library_item.name

    slot = page.presentation.device_slots[0]
    slot_source = page._slot_rows[slot.stable_id]._macro_drag_source
    assert slot_source._macro_drag_payload == slot.stable_id
    assert slot_source._macro_drag_preview_title == "Slot 1"
    assert slot_source._macro_drag_preview_value == "POE-Flasks"

    for source, forbidden_prefix in (
        (action_source, "action-id:"),
        (library_source, "local-macro:"),
        (slot_source, "device-macro-slot:"),
    ):
        preview = page._build_drag_preview(
            source._macro_drag_preview_title,
            source._macro_drag_preview_value,
        )
        visible_text = " ".join(_label_texts(preview))
        assert forbidden_prefix not in visible_text
        assert source._macro_drag_payload not in visible_text
        assert preview.has_css_class("macro-action-drag-preview")
        assert preview.get_accessible_role() is Gtk.AccessibleRole.PRESENTATION


@requires_gtk_display
def test_drag_preview_cleanup_is_idempotent_for_end_and_cancel():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    action = page.presentation.selected_macro.current_actions[0]
    source = page._action_rows[action.stable_id]._macro_drag_source
    preview = page._build_drag_preview("01  Keyboard press", "1")

    class Icon:
        def __init__(self, child):
            self.child = child

        def get_child(self):
            return self.child

        def set_child(self, child):
            self.child = child

    icon = Icon(preview)
    source._macro_drag_preview = preview
    source._macro_drag_icon = icon
    assert page._drag_preview_cancel(source, None, None) is False
    assert icon.child is None
    assert source._macro_drag_preview is None
    assert source._macro_drag_icon is None
    page._drag_preview_end(source, None, False)
    assert source._macro_drag_preview is None


def test_custom_drag_icon_uses_gtk4_drag_icon_without_payload_text():
    source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "Gtk.DragIcon.get_for_drag(drag)" in source
    assert "icon.set_child(preview)" in source
    assert 'connect_after("drag-begin", self._drag_preview_begin)' in source
    assert "source._macro_drag_payload" in source
    assert "_macro_drag_payload" not in source[
        source.index("def _build_drag_preview"):
        source.index("def _drag_preview_begin")
    ]


@requires_gtk_display
def test_left_zone_selects_without_editing_and_right_zone_opens_editor():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    first = page.presentation.selected_macro.current_actions[0]
    second = page.presentation.selected_macro.current_actions[1]
    first_row = page._action_rows[first.stable_id]
    second_row = page._action_rows[second.stable_id]

    class Gesture:
        def __init__(self, widget):
            self._widget = widget

        def get_widget(self):
            return self._widget

    page._select_action_row(second_row)
    assert page.presentation.selected_action_index == 1
    assert page._inline_editor_state is None

    page._action_value_pressed(
        Gesture(first_row._macro_value_body), 1, 8.0, 8.0
    )
    assert page.presentation.selected_action_index == 0
    assert page._inline_editor_state is None

    page._action_value_pressed(
        Gesture(first_row._macro_value_body), 2, 8.0, 8.0
    )
    assert page._inline_editor_state.action_id == first.stable_id
    assert page._inline_editor_state.editor.get_visible()


@requires_gtk_display
def test_action_values_share_one_right_aligned_fixed_width_column_without_dashes():
    window, page = _open_windowed_macros_page()
    try:
        rows = tuple(page._action_rows.values())
        widths = {row._macro_value_body.get_width() for row in rows}
        assert len(widths) == 1
        value_width = widths.pop()
        assert value_width >= 118
        ratios = [value_width / row.get_width() for row in rows]
        # Rows are a little narrower since the staging list reserves its
        # scrollbar and the Macros card grew for the bank scrollbars (the
        # value column itself stays a fixed 118 px).
        assert all(0.20 <= ratio <= 0.30 for ratio in ratios)
        right_edges = []
        for row in rows:
            label = row._macro_value_label
            assert label.get_xalign() == 1
            assert label.get_text() != "—"
            x, _y = _point_in_root(row._macro_value_body, page._click_away_root)
            right_edges.append(round(x + row._macro_value_body.get_width()))
        assert len(set(right_edges)) == 1
        assert [row._macro_value_label.get_text() for row in rows[:3]] == [
            "1",
            "10 ms",
            "1",
        ]

        mouse = next(
            item
            for item in page.presentation.local_macros
            if item.name == "12_MouseLeftClick"
        )
        page.presentation.select_macro(mouse.stable_id)
        assert page._action_rows[
            page.presentation.selected_macro.current_actions[0].stable_id
        ]._macro_value_label.get_text() == "Left button"
        assert all(
            row._macro_value_label.get_text() != "—"
            for row in page._action_rows.values()
        )
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_stable_action_drag_payload_reorders_from_left_zone_only():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    original_ids = page.presentation.selected_macro.current_action_ids
    source_id = original_ids[0]
    target_id = original_ids[-1]
    assert page._drop_on_action(
        None, f"action-id:{source_id}", 4.0, 4.0, target_id
    )
    assert page.presentation.selected_macro.current_action_ids[-1] == source_id
    assert set(page.presentation.selected_macro.current_action_ids) == set(
        original_ids
    )
    moved_row = page._action_rows[source_id]
    assert moved_row._macro_drag_source.get_widget() is moved_row._macro_drag_body
    assert not any(
        isinstance(controller, Gtk.DragSource)
        for controller in _controllers(moved_row._macro_value_body)
    )


@requires_gtk_display
def test_f2_and_value_enter_are_keyboard_inline_edit_alternatives():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    action = page.presentation.selected_macro.current_actions[0]
    row = page._action_rows[action.stable_id]

    class Controller:
        def __init__(self, widget):
            self._widget = widget

        def get_widget(self):
            return self._widget

    assert page._action_zone_key_pressed(
        Controller(row._macro_drag_body), Gdk.KEY_F2, 0, 0
    )
    assert page._inline_editor_state.action_id == action.stable_id
    page._finish_inline_edit(commit=False)
    assert page._action_zone_key_pressed(
        Controller(row._macro_value_body), Gdk.KEY_Return, 0, 0
    )
    assert page._inline_editor_state.action_id == action.stable_id


def test_inline_commit_has_one_consistent_editor_only_signature():
    from venus_profile.gui.macros.page import MacrosPage

    assert tuple(inspect.signature(MacrosPage._commit_inline_action).parameters) == (
        "self",
        "editor",
    )


@requires_gtk_display
@pytest.mark.parametrize(
    "target_name",
    (
        "_sequence_scroll",
        "_up_button",
        "_loop_mode",
        "_record_row",
        "_editor_footer",
        "_revert_macro_button",
        "_left_card",
    ),
)
def test_application_local_capture_click_commits_from_page_regions(target_name):
    window, page = _open_windowed_macros_page()
    try:
        delay_id = page.presentation.selected_macro.current_actions[1].stable_id
        page._begin_inline_action_edit(delay_id)
        editor = page._inline_editor_state.editor
        editor.set_text("250")
        _capture_click(page, getattr(page, target_name))
        assert page._inline_editor_state is None
        assert page.presentation.selected_macro.current_records[1].delay_ms == 250
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_click_inside_entry_and_mouse_dropdown_does_not_close_editor():
    window, page = _open_windowed_macros_page()
    try:
        key_id = page.presentation.selected_macro.current_actions[0].stable_id
        page._begin_inline_action_edit(key_id)
        key_editor = page._inline_editor_state.editor
        pump_until(lambda: _settled(key_editor))
        _capture_click(page, key_editor)
        assert page._inline_editor_state.editor is key_editor

        page._finish_inline_edit(commit=False)
        mouse = next(
            item for item in page.presentation.local_macros
            if item.name == "12_MouseLeftClick"
        )
        page.presentation.select_macro(mouse.stable_id)
        mouse_id = page.presentation.selected_macro.current_actions[0].stable_id
        page._begin_inline_action_edit(mouse_id)
        mouse_editor = page._inline_editor_state.editor
        assert isinstance(mouse_editor, Gtk.DropDown)
        pump_until(lambda: _settled(mouse_editor))
        _capture_click(page, mouse_editor)
        assert page._inline_editor_state.editor is mouse_editor
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_outside_click_and_focus_leave_commit_once_and_do_not_consume_control():
    window, page = _open_windowed_macros_page()
    try:
        key_id = page.presentation.selected_macro.current_actions[0].stable_id
        page._begin_inline_action_edit(key_id)
        editor = page._inline_editor_state.editor
        editor.set_text("7")
        notifications = []
        page.presentation.subscribe(lambda state: notifications.append(state.selected_macro.current_records))
        library_row = next(iter(page._library_rows.values()))
        _capture_click(page, page._insert_buttons["lmb-down"])

        class Controller:
            def get_widget(self):
                return editor

        page._inline_editor_focus_leave(Controller())
        assert len(notifications) == 1
        assert page.presentation.selected_macro.current_records[0].usage == 0x24
        assert page._action_rows[key_id]._macro_value_label.get_text() == "7"
        assert library_row in page._library_rows.values()

        count = page.presentation.selected_macro.current_action_count
        page._insert_buttons["lmb-down"].emit("clicked")
        # The click plus the 10 ms minimum delay placed before it.
        assert page.presentation.selected_macro.current_action_count == count + 2
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_keyboard_friendly_edits_and_inline_validation_behave_normally():
    window, page = _open_windowed_macros_page()
    try:
        key_id = page.presentation.selected_macro.current_actions[0].stable_id
        page._begin_inline_action_edit(key_id)
        editor = page._inline_editor_state.editor
        assert editor.get_text() == "1"
        editor.set_text("7")
        assert page._finish_inline_edit(commit=True) is True
        assert page.presentation.selected_macro.current_records[0].usage == 0x24
        assert page._action_rows[key_id]._macro_value_label.get_text() == "7"

        page._begin_inline_action_edit(key_id)
        editor = page._inline_editor_state.editor
        editor.set_text("A")
        assert page._finish_inline_edit(commit=True) is True
        assert page.presentation.selected_macro.current_records[0].usage == 0x04
        assert page._action_rows[key_id]._macro_value_label.get_text() == "A"

        page._begin_inline_action_edit(key_id)
        editor = page._inline_editor_state.editor
        editor.set_text("not a keyboard key")
        assert page._finish_inline_edit(commit=True) is False
        assert page._inline_editor_state.editor is editor
        assert editor.get_visible() is True
        assert editor.get_text() == "not a keyboard key"
        assert editor.has_css_class("error")
        assert page.presentation.selected_macro.current_records[0].usage == 0x04
        page._finish_inline_edit(commit=False)
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_escape_then_outside_click_never_commits_cancelled_text():
    window, page = _open_windowed_macros_page()
    try:
        key_id = page.presentation.selected_macro.current_actions[0].stable_id
        original = page.presentation.selected_macro.current_records[0]
        page._begin_inline_action_edit(key_id)
        editor = page._inline_editor_state.editor
        editor.set_text("A")

        class Controller:
            def get_widget(self):
                return editor

        assert page._inline_editor_key_pressed(
            Controller(), Gdk.KEY_Escape, 0, 0
        )
        _capture_click(page, page._left_card)
        assert page._inline_editor_state is None
        assert page.presentation.selected_macro.current_records[0] == original
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_delay_edit_and_real_drop_callback_routes_are_safe():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    page._select_action(None, 1)
    selected = page.presentation.selected_macro
    before = selected.current_records[1].delay_ms
    delay_id = selected.current_actions[1].stable_id
    page._begin_inline_action_edit(delay_id)
    page._action_rows[delay_id]._macro_inline_editor.set_text("20")
    page._action_rows[delay_id]._macro_inline_editor.emit("activate")
    assert page.presentation.selected_macro.current_records[1].delay_ms == 20
    assert before != 20
    row = page._action_rows[delay_id]

    class Gesture:
        def get_widget(self):
            return row._macro_value_body

    page._action_value_pressed(Gesture(), 2, 4.0, 4.0)
    assert row._macro_inline_editor.get_visible() is True
    macro = next(item for item in page.presentation.local_macros if item.name == "11_Ctrl1")
    page._drop_on_slot(None, macro.stable_id, 10.0, 10.0, 2)
    assert page.presentation.device_slots[2].loaded_macro_id == macro.stable_id
    target_id = page.presentation.selected_macro.current_actions[2].stable_id
    page._drop_on_action(None, "action:0", 0.0, 0.0, target_id)
    assert page.presentation.selected_macro.current_records[2] == selected.current_records[0]


@requires_gtk_display
def test_reorder_then_double_click_resolves_current_row_metadata():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    action_id = page.presentation.selected_macro.current_actions[1].stable_id
    page.presentation.reorder_action(1, 0)
    row = page._action_rows[action_id]

    class Gesture:
        def get_widget(self):
            return row._macro_value_body

    page._action_value_pressed(Gesture(), 2, 4.0, 4.0)
    assert row._macro_inline_editor.get_visible() is True


@requires_gtk_display
def test_switching_rows_commits_old_editor_by_stable_id_and_opens_new_editor():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    original = page.presentation.selected_macro.current_records
    key_id = page.presentation.selected_macro.current_actions[0].stable_id
    delay_id = page.presentation.selected_macro.current_actions[1].stable_id
    key_row = page._action_rows[key_id]
    key_editor = key_row._macro_inline_editor
    page._begin_inline_action_edit(key_id)
    assert key_editor.get_text() == "1"
    key_editor.set_text("31")

    delay_row = page._action_rows[delay_id]

    class Gesture:
        def get_widget(self):
            return delay_row._macro_value_body

    page._action_value_pressed(Gesture(), 2, 4.0, 4.0)
    current = page.presentation.selected_macro.current_records
    assert current[0].usage == 31
    assert current[1:] == original[1:]
    assert page._action_rows[delay_id] is delay_row
    assert page._inline_editor_state.action_id == delay_id
    assert page._inline_editor_state.editor.get_visible() is True
    assert key_editor.get_visible() is False


@requires_gtk_display
def test_opening_and_closing_keyboard_editor_without_change_does_not_mutate():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    key_id = page.presentation.selected_macro.current_actions[0].stable_id
    notifications = []
    page.presentation.subscribe(lambda state: notifications.append(state.selected_macro.current_records))
    page._begin_inline_action_edit(key_id)
    assert page._inline_editor_state.editor.get_text() == "1"
    page._finish_inline_edit(commit=True)
    assert notifications == []
    assert page.presentation.selected_macro.current_records[0].usage == 30


@requires_gtk_display
def test_focus_leave_commits_once_and_escape_cancels_without_late_commit():
    from venus_profile.gui.macros.page import MacrosPage

    page = MacrosPage(demo_workspace())
    delay_id = page.presentation.selected_macro.current_actions[1].stable_id
    page._begin_inline_action_edit(delay_id)
    editor = page._inline_editor_state.editor
    editor.set_text("25")

    class Controller:
        def get_widget(self):
            return editor

    page._inline_editor_focus_leave(Controller())
    assert page.presentation.selected_macro.current_records[1].delay_ms == 30
    assert page._inline_editor_state is None
    page._inline_editor_focus_leave(Controller())
    assert page.presentation.selected_macro.current_records[1].delay_ms == 30

    current_delay_id = page.presentation.selected_macro.current_actions[1].stable_id
    page._begin_inline_action_edit(current_delay_id)
    cancel_editor = page._inline_editor_state.editor
    cancel_editor.set_text("90")

    class CancelController:
        def get_widget(self):
            return cancel_editor

    assert page._inline_editor_key_pressed(
        CancelController(), Gdk.KEY_Escape, 0, 0
    ) is True
    page._inline_editor_focus_leave(CancelController())
    assert page.presentation.selected_macro.current_records[1].delay_ms == 30
    assert page._inline_editor_state is None


@requires_gtk_display
def test_inline_entry_retains_typed_text_and_focus_without_row_refresh():
    from venus_profile.gui.macros.page import MacrosPage

    window = Gtk.Window()
    window.set_default_size(1100, 780)
    page = MacrosPage(demo_workspace())
    window.set_child(page)
    window.present()
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    try:
        delay_id = page.presentation.selected_macro.current_actions[1].stable_id
        page._begin_inline_action_edit(delay_id)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        editor = page._inline_editor_state.editor
        changed = []
        editor.connect("changed", lambda entry: changed.append(entry.get_text()))
        editor.set_text("250")
        page._refresh(page.presentation)
        assert editor.get_text() == "250"
        assert changed[-1] == "250"
        assert "250" in changed
        assert editor.get_editable() is True
        focus_widget = window.get_focus()
        focus_chain = []
        while focus_widget is not None:
            focus_chain.append(focus_widget)
            focus_widget = focus_widget.get_parent()
        assert editor in focus_chain
    finally:
        window.close()
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)


def test_macros_page_has_two_outer_cards_and_no_hidden_slot_switch_in_source():
    source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    assert "self._build_left_card()" in source
    assert "self._build_right_card()" in source
    assert "_build_slots_expander" not in source
    assert "Gtk.DragSource" in source
    assert "Gtk.DropTarget" in source
    assert "_drop_on_slot" in source
    assert "_drop_on_action" in source
    assert "Record" in source and "Stop" in source


def test_compact_rows_and_drop_handlers_are_stable_in_source():
    page_source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "macros" / "page.py").read_text(encoding="utf-8")
    css_source = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    assert "actions ·" not in page_source
    assert "No assignment in fixture" not in page_source
    assert "target_action_id: str" in page_source
    assert 'value.startswith("action-id:")' in page_source
    assert "def _drop_on_slot(self, _target, value: str, _x: float, _y: float, slot_index: int)" in page_source
    assert "max-height" not in css_source
    assert "max-width" not in css_source


def test_action_reorder_preserves_raw_records_and_self_drop():
    state = MacroPresentationState(demo_workspace())
    original = state.selected_macro.current_records
    state.reorder_action(len(original) - 1, 0)
    assert state.selected_macro.current_records[0] == original[-1]
    state.reorder_action(0, 0)
    assert set(state.selected_macro.current_records) == set(original)


@requires_gtk_display
def test_staging_sequence_fills_creator_height_inside_a_frame():
    window, page = _open_windowed_macros_page()
    try:
        overlay = page._sequence_frame
        assert "macro-sequence-frame" in overlay.get_css_classes()
        assert page._sequence_overlay.get_parent() is overlay
        assert page._sequence_scroll.get_vexpand() is True
        assert overlay.get_vexpand() is True
        # No fixed 210px cap and no spacer stealing the card's spare height.
        assert page._sequence_scroll.get_size_request() == (-1, -1)
        record_row = page._record_row
        ok, record_point = overlay.compute_point(record_row, Graphene.Point())
        assert ok
        gap_to_record_row = -record_point.y - overlay.get_height()
        # The framed viewport ends just above the record row.
        assert 0 <= gap_to_record_row <= 24
        assert overlay.get_height() > 210
    finally:
        window.close()
        _pump_gtk()


def test_staging_frame_css_survives_drag_suppression():
    css = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
    frame = css[css.index(".macro-sequence-frame {"):]
    frame = frame[:frame.index("}")]
    assert "border: 1px solid @border" in frame
    assert "border-radius: 6px" in frame


@requires_gtk_display
def test_action_column_has_explicit_buttons_in_requested_order():
    window, page = _open_windowed_macros_page()
    try:
        column = page._up_button.get_parent()
        labels = []
        child = column.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                labels.append(child.get_label())
            elif isinstance(child, Gtk.Box) and "macro-delay-row" in child.get_css_classes():
                # [Delay][ ms ] row
                assert child.get_first_child() is page._delay_toggle
                assert isinstance(page._delay_toggle, Gtk.ToggleButton)
                assert child.get_last_child() is page._delay_entry
                labels.append(page._delay_toggle.get_label())
            assert not isinstance(child, Gtk.DropDown)
            child = child.get_next_sibling()
        assert labels == ["LMB Down", "LMB Up", "RMB Down", "RMB Up", "Delay",
                          "Move Up", "Move Down", "Delete", "Cut", "Copy", "Paste"]
        assert not hasattr(page, "_add_type")
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
@pytest.mark.parametrize(
    ("key", "expected"),
    (
        ("lmb-down", ActionRecord.mouse_button(1, True)),
        ("lmb-up", ActionRecord.mouse_button(1, False)),
        ("rmb-down", ActionRecord.mouse_button(2, True)),
        ("rmb-up", ActionRecord.mouse_button(2, False)),
    ),
)
def test_insert_buttons_append_their_confirmed_record(key, expected):
    window, page = _open_windowed_macros_page()
    try:
        before = page.presentation.selected_macro.current_records
        page._insert_buttons[key].emit("clicked")
        after = page.presentation.selected_macro.current_records
        # The fixture macro ends with a non-delay action, so the 10 ms minimum
        # delay (neither delay option checked) goes before the new action.
        assert after[:len(before)] == before
        assert after[len(before):] == (ActionRecord.action_delay_ms(10), expected)
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_new_uses_typed_name_and_falls_back_to_default():
    window, page = _open_windowed_macros_page()
    try:
        page._name_entry.set_text("  Flask Rotation ")
        page._new_button.emit("clicked")
        assert page.presentation.selected_macro.name == "Flask Rotation"
        assert page._name_entry.get_text() == "Flask Rotation"
        # Field unchanged (mirrors the selection): default name, not a clone.
        page._new_button.emit("clicked")
        assert page.presentation.selected_macro.name == "New Macro"
        page._name_entry.set_text("")
        page._new_button.emit("clicked")
        assert page.presentation.selected_macro.name == "New Macro 2"
        # An existing name is made unique rather than duplicated.
        page._name_entry.set_text("Flask Rotation")
        page._new_button.emit("clicked")
        assert page.presentation.selected_macro.name == "Flask Rotation 2"
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_new_with_invalid_typed_name_reports_and_creates_nothing():
    window, page = _open_windowed_macros_page()
    try:
        count = len(page.presentation.library.entries)
        page._name_entry.set_text("bad/name")
        page._new_button.emit("clicked")
        assert len(page.presentation.library.entries) == count
    finally:
        window.close()
        _pump_gtk()


def test_revert_macro_undoes_changes_since_selection_even_after_saving():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    poe = next(item for item in state.local_macros if item.name == "POE-Flasks")
    state.select_macro(poe.stable_id)
    original = state.selected_macro.current_records
    assert state.selected_macro.edited is False
    state.add_action(ActionRecord.action_delay_ms(20))
    workspace.save_all()  # the library is saved automatically in the app
    assert state.selected_macro.dirty is False
    assert state.selected_macro.edited is True
    state.revert_selected_macro()
    assert state.selected_macro.current_records == original
    assert state.selected_macro.edited is False


def test_revert_macro_restores_name_through_workspace_references():
    workspace = demo_workspace()
    state = MacroPresentationState(workspace)
    poe = next(item for item in state.local_macros if item.name == "POE-Flasks")
    state.select_macro(poe.stable_id)
    state.rename_selected_macro("Renamed")
    assert state.selected_macro.edited is True
    state.revert_selected_macro()
    assert state.selected_macro.name == "POE-Flasks"


def test_effective_default_delay_matches_the_windows_editor():
    from venus_profile.macro_library import effective_default_delay_ms

    # ldcfg.exe sub_424a30: < 10 -> 10, then round down to a multiple of 10.
    assert [effective_default_delay_ms(v) for v in (0, 1, 9, 10, 15, 250, 999)] == [
        10, 10, 10, 10, 10, 250, 990]


def _empty_selected_macro_page():
    from venus_profile.gui.macros.page import MacrosPage

    window = Gtk.Window()
    page = MacrosPage(temp_workspace_for_macros())
    window.set_child(page)
    window.present()
    _pump_gtk()
    page.presentation.add_local_macro("Delays")
    _pump_gtk()
    return window, page


@requires_gtk_display
def test_record_delays_and_default_delay_are_mutually_exclusive_and_saved():
    from venus_profile.macro_library import DELAY_TYPE_DEFAULT, DELAY_TYPE_NONE, DELAY_TYPE_RECORDED

    window, page = _empty_selected_macro_page()
    try:
        macro = lambda: page.presentation.selected_macro.macro
        assert macro().delay_type == DELAY_TYPE_RECORDED            # new macros record delays by default
        assert page._record_delays.get_active() and not page._delay_toggle.get_active()
        page._record_delays.set_active(False)
        assert macro().delay_type == DELAY_TYPE_NONE
        page._record_delays.set_active(True)
        assert macro().delay_type == DELAY_TYPE_RECORDED
        page._delay_toggle.set_active(True)
        assert macro().delay_type == DELAY_TYPE_DEFAULT
        assert page._record_delays.get_active() is False  # turned off by Delay
        page._record_delays.set_active(True)
        assert macro().delay_type == DELAY_TYPE_RECORDED
        assert page._delay_toggle.get_active() is False  # turned off by Record delays
        page._record_delays.set_active(False)
        assert macro().delay_type == DELAY_TYPE_NONE
        page._delay_entry.set_text("250")
        assert macro().default_inserted_delay_ms == 250
        page._delay_entry.set_text("2a5")  # digits only
        assert macro().default_inserted_delay_ms == 25
        _pump_gtk()
        assert page._delay_entry.get_text() == "25"
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
@pytest.mark.parametrize(("typed", "inserted"), (("", 10), ("1", 10), ("15", 10), ("250", 250), ("999", 990)))
def test_default_delay_goes_between_inserted_clicks(typed, inserted):
    window, page = _empty_selected_macro_page()
    try:
        page._delay_entry.set_text(typed)
        page._delay_toggle.set_active(True)
        for key in ("lmb-down", "lmb-up", "rmb-down"):
            page._insert_buttons[key].emit("clicked")
        records = page.presentation.selected_macro.current_records
        assert records == (
            ActionRecord.mouse_button(1, True), ActionRecord.action_delay_ms(inserted),
            ActionRecord.mouse_button(1, False), ActionRecord.action_delay_ms(inserted),
            ActionRecord.mouse_button(2, True),
        )
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_minimum_10ms_delay_is_inserted_when_both_delay_options_are_off():
    window, page = _empty_selected_macro_page()
    try:
        page._delay_entry.set_text("250")  # ignored while Delay is off
        page._insert_buttons["lmb-down"].emit("clicked")
        page._insert_buttons["lmb-up"].emit("clicked")
        assert page.presentation.selected_macro.current_records == (
            ActionRecord.mouse_button(1, True), ActionRecord.action_delay_ms(10),
            ActionRecord.mouse_button(1, False))
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_selecting_a_macro_shows_its_saved_delay_settings():
    from venus_profile.macro_library import DELAY_TYPE_DEFAULT

    window, page = _empty_selected_macro_page()
    try:
        page._delay_entry.set_text("40")
        page._delay_toggle.set_active(True)
        first = page.presentation.selected_macro.stable_id
        page.presentation.add_local_macro("Other")
        _pump_gtk()
        assert page._delay_toggle.get_active() is False
        page.presentation.select_macro(first)
        _pump_gtk()
        assert page._delay_toggle.get_active() is True
        assert page._delay_entry.get_text() == "40"
        assert page.presentation.selected_macro.macro.delay_type == DELAY_TYPE_DEFAULT
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_name_field_is_usable_with_no_macro_and_creates_the_typed_macro():
    from venus_profile.gui.macros.page import MacrosPage

    window = Gtk.Window()
    page = MacrosPage(temp_workspace_for_macros())
    window.set_child(page)
    window.present()
    _pump_gtk()
    try:
        assert page.presentation.selected_macro is None
        assert page._name_entry.get_placeholder_text() == "Select or create a new macro"
        assert page._name_entry.get_sensitive() is True
        page._name_entry.set_text("Opener")
        page._refresh(page.presentation)  # unrelated refresh keeps the typed name
        assert page._name_entry.get_text() == "Opener"
        page._new_button.emit("clicked")
        assert page.presentation.selected_macro.name == "Opener"
        # Enter with nothing selected also creates.
        page.presentation._set_selected(None)
        page._refresh(page.presentation)
        assert page._name_entry.get_text() == ""
        page._name_entry.set_text("Second")
        page._name_entry.emit("activate")
        assert page.presentation.selected_macro.name == "Second"
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_clicking_a_macro_shows_its_name_in_the_name_field():
    window, page = _open_windowed_macros_page()
    try:
        page._name_entry.set_text("half typed")
        target = next(item for item in page.presentation.local_macros
                      if item.stable_id != page.presentation.selected_macro_id)
        page._library_rows[target.stable_id].emit("clicked")
        assert page._name_entry.get_text() == target.name
    finally:
        window.close()
        _pump_gtk()


def temp_workspace_for_macros():
    from gui_workspace import temp_workspace
    return temp_workspace()


class _FakeKeyController:
    def __init__(self):
        self.time = 0

    def get_current_event_time(self):
        return self.time


def _record(page, events):
    """Feed (time_ms, evdev_code, down) events through the recorder."""
    controller = _FakeKeyController()
    page._recording = []
    page._recording_last_time = None
    page._recording_held = set()
    for time, evdev, down in events:
        controller.time = time
        page._record_key(controller, 0, evdev + 8, 0, down)
    page._stop_recording()
    return page.presentation.selected_macro.current_records


A_KEY, B_KEY = 30, 48  # evdev codes for A and B


@requires_gtk_display
def test_recording_with_record_delays_rounds_measured_time_down_like_windows():
    window, page = _empty_selected_macro_page()
    try:
        page._record_delays.set_active(True)
        records = _record(page, [(1000, A_KEY, True), (1097, A_KEY, False), (1102, B_KEY, True)])
        assert records == (
            ActionRecord.keyboard(0x04, True),   # first event: no delay before it
            ActionRecord.action_delay_ms(90),    # 97 ms -> 90 (rounded down)
            ActionRecord.keyboard(0x04, False),
            ActionRecord.keyboard(0x05, True),   # 5 ms -> 0 -> no delay record
            ActionRecord.keyboard(0x05, False),  # released on Stop
        )
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_recording_with_default_delay_inserts_it_between_actions():
    window, page = _empty_selected_macro_page()
    try:
        page._delay_entry.set_text("5")  # under 10 -> 10 ms, like Windows
        page._delay_toggle.set_active(True)
        records = _record(page, [(1000, A_KEY, True), (3000, A_KEY, False)])
        assert records == (
            ActionRecord.keyboard(0x04, True),
            ActionRecord.action_delay_ms(10),
            ActionRecord.keyboard(0x04, False),
        )
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_recording_with_both_delay_options_off_inserts_minimum_10ms():
    window, page = _empty_selected_macro_page()
    try:
        page._record_delays.set_active(False)                     # on by default for new macros
        records = _record(page, [(1000, A_KEY, True), (3000, A_KEY, False)])
        assert records == (ActionRecord.keyboard(0x04, True), ActionRecord.action_delay_ms(10),
                           ActionRecord.keyboard(0x04, False))
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_recorded_keys_appear_in_the_staging_list_as_they_happen():
    window, page = _empty_selected_macro_page()
    try:
        page._record_delays.set_active(True)
        controller = _FakeKeyController()
        page._recording = []
        page._recording_last_time = None
        page._recording_held = set()
        controller.time = 1000
        page._record_key(controller, 0, A_KEY + 8, 0, True)
        # Before Stop: the key press is already in the macro and the list.
        assert page.presentation.selected_macro.current_records == (ActionRecord.keyboard(0x04, True),)
        assert len(page._action_rows) == 1
        controller.time = 1250
        page._record_key(controller, 0, A_KEY + 8, 0, False)
        assert page.presentation.selected_macro.current_records == (
            ActionRecord.keyboard(0x04, True), ActionRecord.action_delay_ms(250),
            ActionRecord.keyboard(0x04, False))
        assert len(page._action_rows) == 3
        page._stop_recording()
        assert page._recording is None
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_staging_list_scrollbar_does_not_overlay_rows_and_list_is_padded():
    window, page = _open_windowed_macros_page()
    try:
        assert page._sequence_scroll.get_overlay_scrolling() is False
        css = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
        rule = css[css.index("box.macro-action-list {"):]
        assert "padding: 4px" in rule[:rule.index("}")]
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_recording_stops_cleanly_when_the_macro_is_full():
    from venus_profile.macro import ACTION_RECORD_CAPACITY

    window, page = _empty_selected_macro_page()
    try:
        page.presentation.add_actions([ActionRecord.action_delay_ms(10)] * (ACTION_RECORD_CAPACITY - 1))
        controller = _FakeKeyController()
        page._recording = []
        page._recording_last_time = None
        page._recording_held = set()
        controller.time = 1000
        page._record_key(controller, 0, A_KEY + 8, 0, True)   # fills the last record
        assert page.presentation.selected_macro.current_action_count == ACTION_RECORD_CAPACITY
        controller.time = 1100
        page._record_key(controller, 0, A_KEY + 8, 0, False)  # no room: recording stops
        assert page._recording is None
        assert page.presentation.selected_macro.current_action_count == ACTION_RECORD_CAPACITY
    finally:
        window.close()
        _pump_gtk()


def test_friendly_issue_text_explains_unreleased_keys_and_buttons():
    from venus_profile.gui.macros.model import friendly_issue
    from venus_profile.validation import Issue

    assert friendly_issue(Issue("error", "missing-key-release", "keyboard usage 0x04 remains held")) == \
        "Key A is pressed but never released"
    assert friendly_issue(Issue("error", "missing-mouse-release", "mouse mask 0x01 remains held")) == \
        "The left mouse button is pressed but never released"


@requires_gtk_display
def test_status_says_changes_saved_or_explains_why_not():
    window, page = _empty_selected_macro_page()
    try:
        page._insert_buttons["lmb-down"].emit("clicked")
        assert page._macro_status.get_text() == \
            "Not saved yet · The left mouse button is pressed but never released"
        assert "error:" not in page._editor_warning.get_text()
        page._insert_buttons["lmb-up"].emit("clicked")
        page.workspace.save_all()  # what the app's automatic save does
        _pump_gtk()
        assert page._macro_status.get_text().startswith("Changes saved · ")
        assert "Edited" not in page._macro_status.get_text()
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_rows_show_press_release_arrows_and_plain_delay_label():
    window, page = _empty_selected_macro_page()
    try:
        page._insert_buttons["lmb-down"].emit("clicked")
        page._insert_buttons["lmb-up"].emit("clicked")
        _pump_gtk()
        rows = list(page._action_rows.values())

        def texts(row):
            found = []

            def walk(widget):
                if isinstance(widget, Gtk.Label):
                    found.append((widget.get_text(), widget.get_css_classes()))
                child = widget.get_first_child()
                while child is not None:
                    walk(child)
                    child = child.get_next_sibling()

            walk(row)
            return found

        directions = [next(t for t, c in texts(row) if "macro-action-direction" in c) for row in rows]
        kinds = [next(t for t, c in texts(row) if "macro-action-kind" in c) for row in rows]
        assert directions == ["↓", "", "↑"]
        assert kinds == ["Mouse press", "Delay", "Mouse release"]
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_held_key_message_is_hidden_while_recording():
    window, page = _empty_selected_macro_page()
    try:
        controller = _FakeKeyController()
        page._recording = []
        page._recording_last_time = None
        page._recording_held = set()
        controller.time = 1000
        page._record_key(controller, 0, A_KEY + 8, 0, True)
        assert page._editor_warning.get_text() == ""
        assert page._macro_status.get_text().startswith("Recording")
        page._stop_recording()  # releases the held key
        assert page.presentation.selected_macro.unfinished == ()
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_slot_and_library_scrollbars_do_not_overlay_rows():
    window, page = _open_windowed_macros_page()
    try:
        assert page._slot_scroll.get_overlay_scrolling() is False
        assert page._library_scroll.get_overlay_scrolling() is False
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_library_and_slot_columns_share_the_card_width_evenly():
    window, page = _open_windowed_macros_page()
    try:
        library = page._library_scroll.get_parent()
        slots = page._slot_scroll.get_parent()
        assert library.get_parent().get_homogeneous() is True
        assert library.get_width() == slots.get_width()
        # Scrollbars no longer eat the old row width: each column is wider
        # than the original 176px by about one scrollbar.
        assert library.get_width() >= 176 + 10
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_library_and_slot_columns_keep_fixed_positions_when_library_is_empty():
    from gui_workspace import temp_workspace
    from venus_profile.gui.macros.page import MacrosPage

    def columns_geometry(workspace):
        window = Gtk.Window()
        window.set_default_size(1100, 780)
        page = MacrosPage(workspace)
        window.set_child(page)
        window.present()
        _pump_gtk()
        try:
            geometry = []
            for scroll in (page._library_scroll, page._slot_scroll):
                column = scroll.get_parent()
                ok, point = column.compute_point(page, Graphene.Point())
                assert ok
                geometry.append((round(point.x), column.get_width()))
            return geometry
        finally:
            window.close()
            _pump_gtk()

    empty = columns_geometry(temp_workspace())
    populated = columns_geometry(demo_workspace())
    assert empty == populated
    assert empty[0][1] == empty[1][1]  # an even split


@requires_gtk_display
def test_list_rows_keep_their_width_when_scrolling_starts():
    """Scrollbar space is reserved, so rows don't shrink once a list overflows."""
    from venus_profile.gui.macros.page import MacrosPage

    window, page = _empty_selected_macro_page()
    try:
        for scroll in (page._library_scroll, page._slot_scroll, page._sequence_scroll):
            assert scroll.get_policy()[1] == Gtk.PolicyType.ALWAYS
            assert scroll.get_overlay_scrolling() is False
        library_row = lambda: next(iter(page._library_rows.values())).get_width()
        action_row = lambda: next(iter(page._action_rows.values())).get_width()
        page._insert_buttons["lmb-down"].emit("clicked")
        _pump_gtk()
        short_library, short_action = library_row(), action_row()
        for index in range(30):
            page.presentation.add_local_macro(f"Filler {index}")
        page.presentation.select_macro(next(iter(page._library_rows)))
        page.presentation.add_actions([ActionRecord.action_delay_ms(10)] * 40)
        _pump_gtk()
        for scroll in (page._library_scroll, page._sequence_scroll):
            adjustment = scroll.get_vadjustment()
            assert adjustment.get_upper() > adjustment.get_page_size()  # now scrolling
        assert library_row() == short_library
        assert action_row() == short_action
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_idle_reserved_scrollbars_are_marked_and_become_visible_when_scrolling():
    window, page = _empty_selected_macro_page()
    try:
        _pump_gtk()
        assert "scroll-idle" in page._sequence_scroll.get_css_classes()  # empty list: nothing to scroll
        page.presentation.add_actions([ActionRecord.action_delay_ms(10)] * 40)
        _pump_gtk()
        adjustment = page._sequence_scroll.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size()
        assert "scroll-idle" not in page._sequence_scroll.get_css_classes()
        css = (Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "styles.css").read_text(encoding="utf-8")
        assert ".scroll-idle > scrollbar slider" in css
    finally:
        window.close()
        _pump_gtk()


@requires_gtk_display
def test_deleting_a_macro_needs_no_confirmation():
    """User decision 2026-11-19: Delete acts at once (the file goes to the trash)."""
    window, page = _empty_selected_macro_page()
    try:
        page._window = window                      # the in-app path that used to open a dialog
        assert [entry.name for entry in page.workspace.library.entries] == ["Delays"]
        page._delete_library_button.emit("clicked")
        _pump_gtk()
        assert page.workspace.library.entries == []
    finally:
        window.close()
        _pump_gtk()
