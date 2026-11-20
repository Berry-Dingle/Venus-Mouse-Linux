"""Button assignment editor and Settings page bound to real profiles."""

import pytest

from venus_profile import button_functions as bf
from venus_profile.gui.assignment.panel import CATEGORIES, FIRE_TRIGGERS

from gui_display import requires_gtk_display
from gui_workspace import temp_workspace
from macro_workspace import demo_workspace

pytestmark = requires_gtk_display


def _page(workspace=None):
    from venus_profile.gui.buttons.page import ButtonsPage
    workspace = workspace or temp_workspace()
    return ButtonsPage(workspace), workspace


def _select(page, button):
    control = next(item for item in page._controls if item.protocol_button == button)
    page.select_control(control)
    return page._assignment


def _choose(panel, category, function_label=None):
    panel._category.set_selected(CATEGORIES.index(category))
    if function_label is not None:
        labels = [item.label for item in bf.functions_in(category)]
        panel._function.set_selected(labels.index(function_label))


def test_panel_shows_real_factory_assignments():
    page, _ = _page()
    assert _select(page, 7)._current.get_text() == "Key 1"
    assert _select(page, 17)._current.get_text() == "Key Keypad +"
    panel = _select(page, 5)
    assert panel._current.get_text() == "DPI +"
    assert panel._category.get_selected() == CATEGORIES.index(bf.Category.DPI)


def test_assignment_applies_immediately_without_assign_button():
    page, workspace = _page()
    before = workspace.profile(1).to_bytes()
    panel = _select(page, 5)  # DPI+ (not a keyboard key)
    assert not hasattr(panel, "_assign") and not hasattr(panel, "_revert")
    _choose(panel, bf.Category.KEYBOARD)
    # Keyboard needs a key first: nothing is applied yet, a hint is shown.
    assert workspace.profile(1).to_bytes() == before
    assert "key" in panel._message.get_text().lower()
    panel = _select(page, 8)
    _choose(panel, bf.Category.KEYBOARD)
    panel._key_entry.set_text("Space")
    panel._key_entry.emit("activate")
    assert bf.read_assignment(workspace.profile(1), 8).label == "Key Space"
    assert workspace.unsaved_profile_slots() == (1,)
    assert workspace.edited_since_load(1)
    assert "Thumb Button 2 · Key Space" in page._side_grid._buttons["side.thumb-2"].get_tooltip_text()

    _choose(panel, bf.Category.COMBO)
    panel._modifier_buttons[0xE0].set_active(True)
    panel._modifier_buttons[0xE1].set_active(True)
    panel._combo_key.set_text("S")
    panel._combo_key.emit("activate")
    assert bf.read_assignment(workspace.profile(1), 8).device_record == bytes.fromhex("8F 03 16 00")

    _choose(panel, bf.Category.MEDIA, "Volume Up")
    assert bf.read_assignment(workspace.profile(1), 8).function_code == 43

    # The profile bar's Cancel undoes everything since the profile was loaded.
    workspace.cancel_edits(1)
    assert workspace.profile(1).to_bytes() == before


def test_fire_key_and_macro_assignment():
    workspace = demo_workspace()
    page, _ = _page(workspace)
    panel = _select(page, 4)
    _choose(panel, bf.Category.FIRE)
    panel._fire_trigger.set_selected(1)  # right button
    panel._fire_delay.set_value(30)
    assert bf.read_assignment(workspace.profile(1), 4).device_record == bytes.fromhex("99 82 03 03")
    # Shots per press: 1-255 (the record byte, as much as Windows can send); delay 20-250 ms
    # (live 2026-11-20: 0 and 10 ms send only the first click).
    assert panel._fire_count.get_range() == (1, 255) and panel._fire_delay.get_range() == (20, 250)
    panel._fire_count.set_value(12)
    view = bf.read_assignment(workspace.profile(1), 4)
    assert view.fire.count == 12 and view.device_record == bytes.fromhex("99 82 0C 03")
    panel._fire_trigger.set_selected(len(FIRE_TRIGGERS) - 1)
    assert panel._fire_key.get_visible()

    panel = _select(page, 9)
    _choose(panel, bf.Category.MACRO)
    panel._macro_slot.set_selected(2)                              # Slot 2 (entry 0 is the placeholder)
    view = bf.read_assignment(workspace.profile(1), 9)
    assert (view.key_number, view.macro_name) == (1, "Aura-Switch")
    panel._macro_slot.set_selected(6)  # empty Slot 6 is refused without leaving Macro
    assert "empty" in panel._message.get_text()
    assert bf.read_assignment(workspace.profile(1), 9).key_number == 1
    assert panel._selected_category() is bf.Category.MACRO


def test_typing_in_the_fire_fields_applies_without_enter():
    """User report 2026-11-20: typed delay/shots did not enable Apply/Cancel (GTK commits on Enter only)."""
    workspace = demo_workspace()
    page, _ = _page(workspace)
    panel = _select(page, 4)
    _choose(panel, bf.Category.FIRE)
    for text in ("1", "12"):                                    # keystroke by keystroke
        panel._fire_count.set_text(text)
    assert panel._fire_count.get_text() == "12"                 # the field is not rewritten mid-typing
    panel._fire_delay.set_text("")                              # cleared to retype: nothing applied yet
    for text in ("5", "50"):
        panel._fire_delay.set_text(text)
    fire = bf.read_assignment(workspace.profile(1), 4).fire
    assert (fire.count, fire.delay_ms) == (12, 50) and workspace.edited_slots() == (1,)
    panel._fire_count.set_text("150")                           # now inside the range
    assert bf.read_assignment(workspace.profile(1), 4).fire.count == 150
    panel._fire_count.set_text("999")                           # above the byte: clamped to 255
    assert bf.read_assignment(workspace.profile(1), 4).fire.count == 255
    panel._fire_delay.set_text("0")                             # below the working minimum: 20 ms
    assert bf.read_assignment(workspace.profile(1), 4).fire.delay_ms == 20


def test_choosing_macro_waits_for_a_slot_then_any_slot_works():
    """User report 2026-11-19: choosing Macro applied Slot 1 at once and bounced back to Keyboard."""
    workspace = demo_workspace()
    page, _ = _page(workspace)
    panel = _select(page, 10)
    before = bf.read_assignment(workspace.profile(1), 10).function_code
    _choose(panel, bf.Category.MACRO)
    assert bf.read_assignment(workspace.profile(1), 10).function_code == before       # nothing assigned yet
    assert "Choose the device slot" in panel._message.get_text()
    assert panel._selected_category() is bf.Category.MACRO
    assert panel._macro_slot.get_selected() == 0                                      # placeholder shown
    panel._macro_slot.set_selected(1)                                                 # Slot 1 can be chosen
    view = bf.read_assignment(workspace.profile(1), 10)
    assert view.function_code == bf.FUNCTION_MACRO and view.key_number == 0


def test_macros_only_on_the_mouse_can_be_assigned():
    workspace = demo_workspace()
    workspace.library.unload_slot(8)
    workspace.library.mouse_only[8] = bytes.fromhex("840400040400").ljust(200, b"\0")
    page, _ = _page(workspace)
    panel = _select(page, 11)
    panel._refresh_macro_slots()
    assert panel._macro_slot_labels[9] == "Slot 9 · On mouse"
    _choose(panel, bf.Category.MACRO)
    panel._macro_slot.set_selected(9)
    view = bf.read_assignment(workspace.profile(1), 11)
    assert view.function_code == bf.FUNCTION_MACRO and view.key_number == 8
    from venus_profile.apply_plan import button_records
    assert button_records(workspace)[(1, 11)] == bytes.fromhex("91 08 01 00")          # plays once


def test_last_left_click_cannot_be_removed():
    page, workspace = _page()
    panel = _select(page, 1)
    _choose(panel, bf.Category.DISABLE)
    assert "Left Click" in panel._message.get_text()
    assert bf.read_assignment(workspace.profile(1), 1).function_code == bf.FUNCTION_LEFT_CLICK
    assert not workspace.dirty


def test_unknown_function_is_shown_and_kept_until_replaced():
    workspace = temp_workspace()
    profile = workspace.profile(1)
    profile.document.set(bf.button_section(profile, 10), "ButtonFunc", "77")
    workspace.save_all()
    page, _ = _page(workspace)
    panel = _select(page, 10)
    assert panel._current.get_text() == "Unknown function 77 (preserved)"
    assert panel._current_note.get_visible()
    workspace.select_slot(2)
    workspace.select_slot(1)
    assert b"ButtonFunc=77" in workspace.profile(1).to_bytes()
    assert not workspace.dirty


def test_profile_switch_shows_that_profiles_assignments():
    workspace = temp_workspace()
    bf.set_single_key(workspace.profile(3), 7, 0x09)
    workspace.save_all()
    page, _ = _page(workspace)
    panel = _select(page, 7)
    workspace.select_slot(3)
    assert panel._current.get_text() == "Key F"
    assert "Profile 3" in panel._subtitle.get_text()
    assert not workspace.dirty


def test_settings_page_edits_and_never_dirties_on_refresh():
    from venus_profile.gui.settings import SettingsPage
    workspace = temp_workspace()
    page = SettingsPage(workspace)
    for slot in (2, 3, 1):
        workspace.select_slot(slot)
    assert not workspace.dirty
    page._pointer.scale.set_value(15)
    page._scroll.scale.set_value(3)
    page._polling_buttons[1000].set_active(True)
    profile = workspace.profile(1)
    assert (profile.pointer_speed_raw, profile.scroll_speed_raw, profile.polling_rate_raw) == (15, 3, 1)
    assert page._polling_buttons[500].get_active() is False
    workspace.select_slot(2)
    assert page._polling_buttons[500].get_active() is True
    assert page._pointer.value_label.get_text() == "10"


def test_settings_show_unknown_stored_values():
    from venus_profile.gui.settings import SettingsPage
    workspace = temp_workspace()
    profile = workspace.profile(1)
    profile.document.set(profile.group_section, "PollingRate", "3")
    workspace.save_all()
    page = SettingsPage(workspace)
    assert not any(button.get_active() for button in page._polling_buttons.values())
    assert "not a known rate" in page._polling_note.get_text()
    assert b"PollingRate=3" in workspace.profile(1).to_bytes()


def test_selecting_controls_never_changes_the_profile():
    """Live-apply must not fire from refreshes (incl. late GTK notifies)."""
    from gi.repository import GLib, Gtk

    workspace = demo_workspace()
    page, _ = _page(workspace)
    window = Gtk.Window()
    window.set_child(page)
    window.present()
    context = GLib.MainContext.default()
    try:
        before = workspace.profile(1).to_bytes()
        all_before = [workspace.profile(slot).to_bytes() for slot in (1, 2, 3, 4, 5)]
        edited_before = workspace.edited_slots()
        for _round in range(2):
            for control in page._controls:
                page.select_control(control)
                for _ in range(20):
                    context.iteration(False)
        for slot in (2, 3, 1):
            workspace.select_slot(slot)
            for _ in range(20):
                context.iteration(False)
        assert workspace.profile(1).to_bytes() == before
        assert [workspace.profile(slot).to_bytes() for slot in (1, 2, 3, 4, 5)] == all_before
        assert workspace.edited_slots() == edited_before
    finally:
        window.destroy()


def test_settings_marks_pc_only_pointer_settings():
    from venus_profile.gui.settings import SettingsPage
    page = SettingsPage(temp_workspace())
    tagged = {row: row.pc_tag is not None for row in (page._pointer, page._acceleration, page._scroll, page._double_click)}
    assert tagged[page._pointer] and tagged[page._acceleration] and tagged[page._double_click]
    assert tagged[page._scroll] is False  # scroll speed is written to the mouse (0x0020)
    assert page._pointer.pc_tag.get_label() == "PC setting"
    assert "never sent to the mouse" in page._pointer.pc_tag.get_tooltip_text()


def test_viewing_a_factory_zero_ms_fire_key_changes_nothing():
    """The factory record 99 81 03 00 (0 ms) is shown at the 20 ms minimum but not rewritten until edited."""
    workspace = demo_workspace()
    before, edited = workspace.profile(1).to_bytes(), workspace.edited_slots()   # demo starts with its own edits
    assert bf.read_assignment(workspace.profile(1), 4).fire.delay_ms == 0
    page, _ = _page(workspace)
    panel = _select(page, 4)
    assert panel._fire_delay.get_value() == 20
    assert workspace.profile(1).to_bytes() == before and workspace.edited_slots() == edited
