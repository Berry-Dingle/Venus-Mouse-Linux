"""Application window: automatic working set, Save As / Load File / Cancel, closing."""

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from venus_profile import button_functions as bf  # noqa: E402
from venus_profile.app_store import AppStore  # noqa: E402
from venus_profile.workspace import VenusWorkspace  # noqa: E402

from gui_display import requires_gtk_display  # noqa: E402
from gui_workspace import shared_application  # noqa: E402

pytestmark = requires_gtk_display


def _pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def window(tmp_path):
    from venus_profile.gui.application import VenusWindow
    workspace = VenusWorkspace.open(AppStore(tmp_path / "store"))
    win = VenusWindow(shared_application(), workspace)
    win.present()
    _pump()
    yield win
    win._allow_close = True
    win.close()
    _pump()


def dayz(project_root) -> Path:
    return project_root / "investigation" / "custom-profiles" / "DayZ.pfd"


def _library_dir() -> Path:
    from venus_profile.app_store import default_profile_library_dir
    return default_profile_library_dir()


def test_startup_has_no_save_button_and_shows_loaded_profile(window):
    assert not hasattr(window, "_loaded_label")
    # Profile pills sit at the left of the profile action bar; nav is in the header.
    assert window._profile_pills.get_parent() is window._profile_buttons["save-as"].get_parent()
    assert window._nav_buttons["buttons"].get_ancestor(Adw.HeaderBar) is not None
    assert set(window.pages) == {"buttons", "dpi", "lighting", "macros", "settings"}
    assert not hasattr(window, "_save_button")
    assert "save" not in window._actions
    assert window._profile_pills.button(1).get_tooltip_text() == "Profile 1 · Factory profile"
    assert window._actions["cancel-edits"].get_enabled() is False
    assert window._profile_buttons["apply"].get_sensitive() is False
    labels = [button.get_label() for button in window._profile_buttons.values()]
    assert labels == ["Save As", "Load File", "Restore", "Reset All", "Cancel", "Apply"]
    assert [window._profile_pills.button(n).get_label() for n in range(1, 6)] == ["1", "2", "3", "4", "5"]


def test_edits_are_saved_automatically_and_survive_restart(window):
    from gui_display import pump_until

    window.workspace.select_slot(2)
    window.pages["settings"]._polling_buttons[1000].set_active(True)
    assert window._profile_pills.button(2).get_label() == "2•"
    assert window._profile_pills.button(2).get_tooltip_text() == "Profile 2 · Factory profile, edited"
    assert window._actions["cancel-edits"].get_enabled() is True
    # Debounced automatic save; no Save button involved.
    pump_until(lambda: not window.workspace.dirty, timeout_ms=3000)
    reopened = VenusWorkspace.open(window.workspace.store)
    assert reopened.profile(2).polling_rate_raw == 1
    assert reopened.selected_slot == 2
    # Still marked as edited relative to the loaded profile (Cancel can undo).
    assert window._profile_pills.button(2).get_label() == "2•"


def test_profile_pills_switch_every_page(window):
    window._profile_pills.button(3).emit("clicked")
    assert window.workspace.selected_slot == 3
    assert window.pages["lighting"].presentation.colour_hex == "#00FF00"
    assert "Profile 3" in window.pages["buttons"]._assignment._subtitle.get_text()
    assert not window.workspace.dirty


def test_load_file_into_any_slot_names_it_and_leaves_source_untouched(window, project_root):
    source = dayz(project_root)
    before = source.read_bytes()
    window.load_profile(source, 4)
    assert bf.read_assignment(window.workspace.profile(4), 7).label == "Key F"
    assert source.read_bytes() == before
    assert window.workspace.loaded_name(4) == "DayZ"
    assert window.workspace.edited_since_load(4) is False
    assert window._profile_pills.button(4).get_tooltip_text() == "Profile 4 · DayZ"
    window.autosave_now()
    assert source.read_bytes() == before
    assert VenusWorkspace.open(window.workspace.store).profile(4).to_bytes() == window.workspace.profile(4).to_bytes()


def test_load_over_edited_slot_asks_first(window, project_root):
    window.workspace.profile(1).set_pointer_speed(5)
    window.workspace.edited()
    window.load_profile(dayz(project_root), 1)
    assert window.workspace.profile(1).pointer_speed_raw == 5  # not replaced yet
    window.load_profile(dayz(project_root), 1, confirmed=True)
    assert window.workspace.profile(1).pointer_speed_raw == 10


def test_invalid_load_keeps_current_profile(window, tmp_path):
    bad = tmp_path / "bad.pfd"
    bad.write_bytes(b"[GROUP]\r\nnot=valid\r\n")
    before = window.workspace.profile(1).to_bytes()
    window.load_profile(bad, 1)
    assert window.workspace.profile(1).to_bytes() == before
    assert window.workspace.loaded_name(1) == "Factory profile"


def test_save_as_writes_named_portable_file_and_slot_takes_its_name(window):
    window.workspace.profile(2).set_pointer_speed(7)
    window.workspace.edited()
    destination = _library_dir() / "Raid Setup.pfd"
    assert window.save_profile_as(destination, 2) is True
    assert b"[GROUP]" in destination.read_bytes()
    assert window.workspace.loaded_name(2) == "Raid Setup"
    assert window.workspace.edited_since_load(2) is False
    # The named file is not touched by later edits (they go to the working slot).
    saved = destination.read_bytes()
    window.workspace.profile(2).set_pointer_speed(9)
    window.workspace.edited()
    window.autosave_now()
    assert destination.read_bytes() == saved


def test_save_as_never_replaces_without_confirmation(window, tmp_path):
    destination = tmp_path / "Existing.pfd"
    assert window.save_profile_as(destination, 2) is True
    original = destination.read_bytes()
    window.workspace.profile(3).set_pointer_speed(4)
    window.workspace.edited()
    # Not confirmed: our own Replace? dialog is shown and nothing is written.
    assert window.save_profile_as(destination, 3) is False
    assert destination.read_bytes() == original
    # Confirmed (by the save dialog or our Replace dialog): replaced.
    assert window.save_profile_as(destination, 3, confirmed=True) is True
    assert destination.read_bytes() != original
    assert window.workspace.loaded_name(3) == "Existing"


def test_save_as_appends_pfd_and_rechecks_the_new_name(window, tmp_path):
    (tmp_path / "Named.pfd").write_bytes(b"keep")
    # The dialog confirmed "Named" (no suffix); "Named.pfd" was not confirmed.
    assert window.save_profile_as(tmp_path / "Named", 1, dialog_confirmed=True) is False
    assert (tmp_path / "Named.pfd").read_bytes() == b"keep"
    assert window.save_profile_as(tmp_path / "Fresh", 1, dialog_confirmed=False) is True
    assert (tmp_path / "Fresh.pfd").exists()


def test_cancel_returns_to_the_loaded_profile(window, project_root):
    window.load_profile(dayz(project_root), 1)
    loaded = window.workspace.profile(1).to_bytes()
    window.workspace.profile(1).set_pointer_speed(3)
    window.workspace.edited()
    window.autosave_now()
    assert window.workspace.edited_since_load(1) is True
    window.workspace.cancel_edits(1)
    assert window.workspace.profile(1).to_bytes() == loaded
    assert window.workspace.loaded_name(1) == "DayZ"
    assert window._actions["cancel-edits"].get_enabled() is False
    window.autosave_now()
    assert VenusWorkspace.open(window.workspace.store).profile(1).to_bytes() == loaded


def test_restore_and_reset_all_do_not_touch_named_files(window):
    destination = _library_dir() / "Mine.pfd"
    window.workspace.profile(1).set_led_color_hex("#123456")
    window.workspace.edited()
    window.save_profile_as(destination, 1)
    saved = destination.read_bytes()
    window.workspace.restore_defaults(1)
    assert window.workspace.profile(1).led_color_hex == "#FF0000"
    assert window.workspace.loaded_name(1) == "Factory profile"
    assert window.workspace.edited_since_load(1) is False
    window.workspace.profile(3).set_pointer_speed(2)
    window.workspace.reset_all()
    assert not window.workspace.edited_slots()
    assert destination.read_bytes() == saved


def test_close_saves_pending_edits_without_asking(window):
    window.workspace.profile(2).set_scroll_speed(5)
    window.workspace.library.create("Pending")
    window.workspace.edited()
    assert window._close_requested(window) is False  # closes immediately
    assert not window.workspace.dirty
    reopened = VenusWorkspace.open(window.workspace.store)
    assert reopened.profile(2).scroll_speed_raw == 5
    assert [entry.name for entry in reopened.library.entries] == ["Pending"]


def test_close_is_held_when_saving_fails(window, monkeypatch):
    from venus_profile.profile_session import ProfileSaveError

    window.workspace.profile(2).set_scroll_speed(5)
    window.workspace.edited()

    def failing_save():
        raise ProfileSaveError("disk full")

    monkeypatch.setattr(window.workspace, "save_all", failing_save)
    assert window._close_requested(window) is True  # held: offers Keep Open / Close Anyway
    window._close_response(None, "cancel")
    assert window.workspace.dirty


def test_close_without_changes_is_immediate(window):
    assert window._close_requested(window) is False


def test_macro_import_and_windows_export(window, project_root, tmp_path):
    window.import_macro_files([project_root / "ProfilesMacros" / "POE-Flasks.MSMACRO"])
    assert [entry.name for entry in window.workspace.library.entries] == ["POE-Flasks"]
    window.workspace.load_macro_into_slot(window.workspace.library.entries[0].macro_id, 0)
    window.export_macrodb(tmp_path / "MacroDB")
    assert (tmp_path / "MacroDB" / "MacroSet.MSDB").exists()


def test_apply_preview_is_read_only_text(window):
    from venus_profile.gui.apply_preview import preview_text
    text = preview_text(window.workspace)
    assert text.startswith("Nothing in this list is sent to the mouse.")
    assert "Before device Apply can be enabled" in text


def test_profile_pills_match_action_button_height(window):
    pill = window._profile_pills.button(1)
    action = window._profile_buttons["save-as"]
    assert pill.measure(Gtk.Orientation.VERTICAL, -1)[0] == action.measure(Gtk.Orientation.VERTICAL, -1)[0]


def test_unfinished_macro_waits_quietly_instead_of_showing_a_dialog(window, monkeypatch):
    from venus_profile.actions import ActionRecord

    alerts = []
    monkeypatch.setattr(window, "_alert", lambda heading, body: alerts.append((heading, body)))
    entry = window.workspace.library.create("Half")
    entry.macro.set_actions([ActionRecord.keyboard(0x04, True)])
    window.workspace.notify("macros")
    assert window.autosave_now() is False
    assert alerts == []  # no focus-stealing dialog mid-recording
    entry.macro.set_actions([ActionRecord.keyboard(0x04, True), ActionRecord.keyboard(0x04, False)])
    window.workspace.notify("macros")
    assert window.autosave_now() is True
    assert not window.workspace.dirty


def test_close_explains_an_unfinished_macro(window):
    from venus_profile.actions import ActionRecord

    entry = window.workspace.library.create("Half")
    entry.macro.set_actions([ActionRecord.keyboard(0x04, True)])
    window.workspace.notify("macros")
    assert window.pages["macros"].presentation.unfinished_macro_problems() == [
        "Half: Key A is pressed but never released"]
    assert window._close_requested(window) is True  # held with a plain-language reason


def test_macro_trash_failure_is_reported_not_swallowed(window, monkeypatch):
    from venus_profile.macro_library import MacroLibrary, MacroLibraryError

    def failing_trash(path):
        raise MacroLibraryError(f"could not move {path.name} to the trash: not supported")

    alerts = []
    monkeypatch.setattr(window, "_alert", lambda heading, body: alerts.append((heading, body)))
    monkeypatch.setattr(MacroLibrary, "trash", staticmethod(failing_trash))
    entry = window.workspace.library.create("Doomed")
    assert window.autosave_now() is True
    window.workspace.delete_macro(entry.macro_id)
    assert window.autosave_now() is False
    assert alerts and "trash" in alerts[0][1]
    assert (window.workspace.library.folder / "Doomed.MSMACRO").exists()  # nothing lost
