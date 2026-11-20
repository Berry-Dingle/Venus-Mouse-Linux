"""Offline workspace: persistence, import/export, macro library and bank."""

from pathlib import Path

import pytest

from venus_profile import button_functions as bf
from venus_profile.actions import ActionRecord
from venus_profile.app_store import AppStore, StoreError, factory_profile_bytes
from venus_profile.macro import MacroFile
from venus_profile.macro_library import MacroLibraryError, validate_macro_name
from venus_profile.profile_session import DirtyProfileReplacementError, ProfileSaveError
from venus_profile.workspace import VenusWorkspace


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "store")


def dayz(project_root):
    return project_root / "investigation" / "custom-profiles" / "DayZ.pfd"


def macro_fixture(project_root, name):
    return project_root / "ProfilesMacros" / name


def test_first_run_seeds_and_persists_factory_profiles(store):
    workspace = VenusWorkspace.open(store)
    assert workspace.startup.seeded == (1, 2, 3, 4, 5)
    assert not workspace.dirty
    for slot in range(1, 6):
        data = store.profile_path(slot).read_bytes()
        assert f"[GROUP{slot - 1}]".encode() in data
    # Slot 1 keeps the factory bytes exactly (already numbered GROUP0).
    assert store.profile_path(1).read_bytes() == factory_profile_bytes(1)
    reopened = VenusWorkspace.open(store)
    assert reopened.startup.restored == (1, 2, 3, 4, 5)
    assert not reopened.dirty


def test_edit_save_and_restore_on_next_launch(store):
    workspace = VenusWorkspace.open(store)
    workspace.select_slot(3)
    workspace.profile().set_dpi_stage_dpi(2, 1600)
    assert workspace.unsaved_profile_slots() == (3,)
    workspace.save_all()
    assert not workspace.dirty
    reopened = VenusWorkspace.open(store)
    assert reopened.selected_slot == 3
    assert reopened.profile(3).dpi_stage_dpi(2) == 1600
    assert reopened.profile(1).dpi_stage_dpi(2) != 1600  # profile isolation


def test_import_is_an_unsaved_change_and_never_touches_source(store, project_root):
    source = dayz(project_root)
    original = source.read_bytes()
    workspace = VenusWorkspace.open(store)
    workspace.import_profile(source, slot=4)
    assert workspace.unsaved_profile_slots() == (4,)
    assert workspace.session(4).origin == source
    assert b"[GROUP3]" in workspace.profile(4).to_bytes()
    assert source.read_bytes() == original
    stored_before = store.profile_path(4).read_bytes()
    workspace.revert_profile(4)
    assert workspace.profile(4).to_bytes() == stored_before
    workspace.import_profile(source, slot=4)
    workspace.save_all()
    assert source.read_bytes() == original
    reopened = VenusWorkspace.open(store)
    assert bf.read_assignment(reopened.profile(4), 7).label == "Key F"
    assert reopened.session(4).origin == source


def test_dirty_slot_requires_confirmation_before_import(store, project_root):
    workspace = VenusWorkspace.open(store)
    workspace.profile(2).set_polling_rate_hz(1000)
    with pytest.raises(DirtyProfileReplacementError):
        workspace.import_profile(dayz(project_root), slot=2)
    assert workspace.profile(2).polling_rate_raw == 1
    workspace.import_profile(dayz(project_root), slot=2, confirm_replace_dirty=True)
    assert workspace.profile(2).polling_rate_raw == 2


def test_restore_defaults_and_reset_all(store):
    workspace = VenusWorkspace.open(store)
    workspace.profile(5).set_led_color_hex("#123456")
    workspace.save_all()
    workspace.restore_defaults(5)
    assert workspace.unsaved_profile_slots() == (5,)
    assert workspace.profile(5).led_color_hex != "#123456"
    workspace.save_all()
    workspace.profile(1).set_brightness(0)
    workspace.reset_all()
    assert workspace.profile(1).brightness_raw == 2
    assert not workspace.unsaved_profile_slots() or workspace.unsaved_profile_slots() == (1,)


def test_exports_are_no_clobber_copies(store, tmp_path):
    workspace = VenusWorkspace.open(store)
    destination = tmp_path / "copy.pfd"
    workspace.export_profile(destination, portable=True, slot=2)
    assert b"[GROUP]" in destination.read_bytes()
    with pytest.raises(ProfileSaveError):
        workspace.export_profile(destination, portable=False, slot=2)
    assert not workspace.dirty


def test_store_refuses_writes_outside_its_root(store, tmp_path):
    with pytest.raises(StoreError):
        store.write_bytes(tmp_path / "elsewhere.pfd", b"x")
    workspace = VenusWorkspace.open(store)
    session = workspace.session(1)
    session.path = tmp_path / "user.pfd"
    with pytest.raises(ProfileSaveError):
        session.save_owned(store)


def test_new_macros_record_delays_by_default(store):
    from venus_profile.macro_library import DELAY_TYPE_RECORDED
    workspace = VenusWorkspace.open(store)
    entry = workspace.library.create("Timed")
    assert entry.macro.delay_type == DELAY_TYPE_RECORDED
    workspace.save_all()
    reopened = VenusWorkspace.open(store).library.find_by_name("Timed")
    assert reopened.macro.delay_type == DELAY_TYPE_RECORDED         # saved, so it survives a restart


def test_macro_library_persists_unlimited_entries(store):
    workspace = VenusWorkspace.open(store)
    library = workspace.library
    created = [library.create("Macro") for _ in range(20)]
    assert len({entry.name for entry in created}) == 20
    assert workspace.macros_dirty
    workspace.save_all()
    reopened = VenusWorkspace.open(store)
    assert len(reopened.library.entries) == 20
    assert not reopened.macros_dirty


def test_macro_names_are_validated():
    assert validate_macro_name("  POE Flasks ") == "POE Flasks"
    for bad in ("", "a/b", "Café", "x" * 49, "dots."):
        with pytest.raises(MacroLibraryError):
            validate_macro_name(bad)


def test_import_windows_macros_and_bank_references(store, project_root):
    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    second = workspace.library.import_file(macro_fixture(project_root, "Aura-Switch.MSMACRO"))
    workspace.load_macro_into_slot(entry.macro_id, 0)
    workspace.assign_macro_to_button(0, 1, 7)
    workspace.assign_macro_to_button(0, 2, 9)
    assert {(r.profile, r.button) for r in workspace.references_to_slot(0)} == {(1, 7), (2, 9)}
    # Replace: buttons follow the slot and receive the new name.
    assert workspace.load_macro_into_slot(second.macro_id, 0) == 2
    assert all(r.macro_name == "Aura-Switch" for r in workspace.references_to_slot(0))
    assert workspace.library.bank.slots[0] == second.macro_id
    # Swap: buttons keep running the same macro.
    workspace.load_macro_into_slot(entry.macro_id, 5)
    workspace.swap_slots(0, 5)
    assert workspace.references_to_slot(5) and not workspace.references_to_slot(0)
    assert all(r.macro_name == "Aura-Switch" for r in workspace.references_to_slot(5))
    # A macro occupies only one slot.
    workspace.load_macro_into_slot(second.macro_id, 9)
    assert workspace.library.bank.slots.count(second.macro_id) == 1
    assert all(r.slot == 9 for r in workspace.macro_references())
    # Rename propagates to references.
    workspace.rename_macro(second.macro_id, "Aura")
    assert all(r.macro_name == "Aura" for r in workspace.macro_references())
    # Unload disables the referencing buttons explicitly.
    assert workspace.unload_slot(9) == 2
    assert not workspace.macro_references()
    assert bf.read_assignment(workspace.profile(1), 7).function_code == bf.FUNCTION_DISABLE
    assert workspace.reference_mismatches() == ()


def test_macro_set_and_windows_macrodb_round_trip(store, project_root, tmp_path):
    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    workspace.load_macro_into_slot(entry.macro_id, 2)
    macro_set = workspace.library.macroset()
    assert macro_set.names[2] == "POE-Flasks" and macro_set.names.count("") == 14
    exported = workspace.library.export_windows_macrodb(tmp_path / "MacroDB")
    assert (exported / "POE-Flasks.MSMACRO").read_bytes() == entry.macro.to_bytes()
    with pytest.raises(MacroLibraryError):
        workspace.library.export_windows_macrodb(tmp_path / "MacroDB")
    other = VenusWorkspace.open(AppStore(tmp_path / "other"))
    imported, notes = other.library.import_macrodb(exported, populate_bank=True)
    assert [item.name for item in imported] == ["POE-Flasks"] and notes == []
    assert other.library.slot_macro(2).name == "POE-Flasks"


def test_deleted_macros_go_to_the_trash(store, project_root, tmp_path, monkeypatch):
    from venus_profile.macro_library import MacroLibrary

    trash = tmp_path / "Trash"
    trashed = []

    def fake_trash(path):
        trashed.append(path)
        trash.mkdir(exist_ok=True)
        path.rename(trash / path.name)

    monkeypatch.setattr(MacroLibrary, "trash", staticmethod(fake_trash))
    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    workspace.save_all()
    library_file = store.macro_library_dir / "POE-Flasks.MSMACRO"
    assert library_file.exists()
    workspace.delete_macro(entry.macro_id)
    assert workspace.macros_dirty
    workspace.save_all()
    assert trashed == [library_file]
    assert not library_file.exists()
    assert (trash / "POE-Flasks.MSMACRO").exists()  # recoverable


def test_invalid_macro_import_is_rejected(store, tmp_path):
    bad = tmp_path / "Bad.MSMACRO"
    macro = MacroFile.create([], macro_file_path="Bad")
    data = macro.to_bytes().replace(b"Count=0", b"Count=99")
    bad.write_bytes(data)
    workspace = VenusWorkspace.open(store)
    with pytest.raises(MacroLibraryError):
        workspace.library.import_file(bad)
    assert workspace.library.entries == []


def test_corrupt_stored_profile_falls_back_to_factory(store):
    VenusWorkspace.open(store)
    store.profile_path(2).write_bytes(b"[GROUP1]\r\nnot a profile\r\n")
    reopened = VenusWorkspace.open(store)
    assert 2 in reopened.startup.seeded
    assert any("Profile 2" in problem for problem in reopened.startup.problems)
    assert b"[GROUP1]" in reopened.profile(2).to_bytes()


def test_macro_slot_reference_warnings(store, project_root):
    workspace = VenusWorkspace.open(store)
    bf.set_macro(workspace.profile(1), 8, 4, "Missing")
    assert any("empty macro slot 5" in item for item in workspace.validation_problems())
    assert workspace.reference_mismatches()
    workspace.save_all()  # warnings do not block saving
    assert not workspace.dirty


def test_windows_ansi_profile_with_accented_macro_name_round_trips(store, project_root, tmp_path):
    source = project_root / "investigation" / "custom-profiles" / "DayZ.pfd"
    data = source.read_bytes().replace(b"MacroName=\r\n", "MacroName=Café\r\n".encode("cp1252"), 1)
    accented = tmp_path / "Accented.pfd"
    accented.write_bytes(data)
    workspace = VenusWorkspace.open(store)
    workspace.import_profile(accented, slot=2)
    profile = workspace.profile(2)
    assert "Café".encode("cp1252") in profile.to_bytes()
    destination = tmp_path / "accented-export.pfd"
    workspace.export_profile(destination, portable=True, slot=2)
    assert destination.read_bytes() == data


# ---------------------------------------------------------------- Windows-style profile files

def test_default_profile_library_is_documents_venusmouse_profiles(monkeypatch, tmp_path):
    from venus_profile.app_store import PROFILE_LIBRARY_ENV, default_profile_library_dir

    monkeypatch.setenv(PROFILE_LIBRARY_ENV, str(tmp_path / "lib"))
    assert default_profile_library_dir() == tmp_path / "lib"
    monkeypatch.delenv(PROFILE_LIBRARY_ENV)
    path = default_profile_library_dir()
    assert path.parts[-2:] == ("VenusMouse", "Profiles")


def test_save_profile_as_refuses_existing_file_unless_replace_is_explicit(store, tmp_path):
    workspace = VenusWorkspace.open(store)
    target = tmp_path / "Named.pfd"
    target.write_bytes(b"user data")
    with pytest.raises(ProfileSaveError):
        workspace.save_profile_as(target, slot=1)
    assert target.read_bytes() == b"user data"
    with pytest.raises(ProfileSaveError):
        workspace.save_profile_as(target, slot=1, replace_existing="yes")  # must be literally True
    assert target.read_bytes() == b"user data"
    workspace.save_profile_as(target, slot=1, replace_existing=True)
    assert b"[GROUP]" in target.read_bytes()
    assert not list(tmp_path.glob(".Named.pfd.*"))  # no temp files left behind


def test_save_profile_as_never_changes_application_store_files(store, tmp_path):
    workspace = VenusWorkspace.open(store)
    working = store.profile_path(2).read_bytes()
    workspace.profile(2).set_pointer_speed(6)
    workspace.edited()
    workspace.save_profile_as(tmp_path / "Copy.pfd", slot=2)
    assert store.profile_path(2).read_bytes() == working  # only autosave writes the slot


def test_load_profile_sets_baseline_and_cancel_returns_to_it(store, project_root):
    workspace = VenusWorkspace.open(store)
    source = dayz(project_root)
    before = source.read_bytes()
    workspace.load_profile(source, slot=3)
    loaded = workspace.profile(3).to_bytes()
    assert workspace.loaded_name(3) == "DayZ"
    assert workspace.edited_slots() == ()
    workspace.profile(3).set_scroll_speed(9)
    workspace.edited()
    assert workspace.edited_slots() == (3,)
    workspace.save_all()
    assert workspace.edited_slots() == (3,)  # persisted, still edited vs loaded
    workspace.cancel_edits(3)
    assert workspace.profile(3).to_bytes() == loaded
    assert workspace.loaded_name(3) == "DayZ"
    assert source.read_bytes() == before


def test_cancel_without_edits_is_a_no_op(store):
    workspace = VenusWorkspace.open(store)
    events = []
    workspace.subscribe(events.append)
    workspace.cancel_edits(1)
    assert events == []


def test_unfinished_macro_never_blocks_saving_profiles(store):
    from venus_profile.actions import ActionRecord

    workspace = VenusWorkspace.open(store)
    entry = workspace.library.create("Half")
    entry.macro.set_actions([ActionRecord.keyboard(0x04, True)])  # press, no release yet
    workspace.profile(2).set_pointer_speed(7)
    workspace.edited()
    with pytest.raises(ProfileSaveError):
        workspace.save_all()
    assert VenusWorkspace.open(store).profile(2).pointer_speed_raw == 7  # profile still saved
    assert workspace.library.dirty  # the macro waits until it is complete



# ---------------------------------------------------------------- Documents macro library

def test_default_macro_library_is_documents_venusmouse_macros(monkeypatch, tmp_path):
    from venus_profile.app_store import MACRO_LIBRARY_ENV, default_macro_library_dir

    monkeypatch.setenv(MACRO_LIBRARY_ENV, str(tmp_path / "lib"))
    assert default_macro_library_dir() == tmp_path / "lib"
    assert AppStore().macro_library_dir == tmp_path / "lib"  # the app's default store
    monkeypatch.delenv(MACRO_LIBRARY_ENV)
    assert default_macro_library_dir().parts[-2:] == ("VenusMouse", "Macros")
    assert AppStore(tmp_path / "x").macro_library_dir == tmp_path / "x" / "VenusMouse" / "Macros"


def test_library_is_named_windows_files_and_macroset(store, project_root):
    from venus_profile.macroset import MacroSet

    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    new = workspace.library.create("Opener")
    new.macro.set_actions([ActionRecord.keyboard(0x04, True), ActionRecord.keyboard(0x04, False)])
    workspace.load_macro_into_slot(entry.macro_id, 2)
    workspace.save_all()
    folder = store.macro_library_dir
    assert sorted(path.name for path in folder.iterdir()) == ["MacroSet.MSDB", "Opener.MSMACRO", "POE-Flasks.MSMACRO"]
    assert (folder / "POE-Flasks.MSMACRO").read_bytes() == entry.macro.to_bytes()
    assert MacroSet.from_path(folder / "MacroSet.MSDB").names[2] == "POE-Flasks"


def test_copying_the_folder_carries_library_and_slots(store, project_root, tmp_path):
    import shutil

    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    workspace.load_macro_into_slot(entry.macro_id, 4)
    workspace.save_all()
    copied = tmp_path / "other-computer" / "Macros"
    shutil.copytree(store.macro_library_dir, copied)
    other = VenusWorkspace.open(AppStore(tmp_path / "other-store", macro_library_dir=copied))
    assert [item.name for item in other.library.entries] == ["POE-Flasks"]
    assert other.library.slot_macro(4).name == "POE-Flasks"
    assert other.startup.problems == ()


def test_files_dropped_into_the_folder_join_the_library(store, project_root):
    import shutil

    store.macro_library_dir.mkdir(parents=True)
    shutil.copy(macro_fixture(project_root, "POE-Flasks.MSMACRO"), store.macro_library_dir / "Dropped In.MSMACRO")
    workspace = VenusWorkspace.open(store)
    assert [item.name for item in workspace.library.entries] == ["Dropped In"]


def test_rename_renames_the_file(store, project_root):
    workspace = VenusWorkspace.open(store)
    entry = workspace.library.import_file(macro_fixture(project_root, "POE-Flasks.MSMACRO"))
    workspace.save_all()
    workspace.rename_macro(entry.macro_id, "Flasks")
    workspace.save_all()
    names = sorted(path.name for path in store.macro_library_dir.glob("*.MSMACRO"))
    assert names == ["Flasks.MSMACRO"]


def test_unknown_file_with_the_same_name_is_never_overwritten(store, project_root):
    workspace = VenusWorkspace.open(store)
    store.macro_library_dir.mkdir(parents=True, exist_ok=True)
    foreign = store.macro_library_dir / "Opener.MSMACRO"
    entry = workspace.library.create("Opener")  # created before the file appeared
    entry.macro.set_actions([ActionRecord.keyboard(0x04, True), ActionRecord.keyboard(0x04, False)])
    foreign.write_bytes(b"user's own file")
    with pytest.raises(ProfileSaveError):
        workspace.save_all()
    assert foreign.read_bytes() == b"user's own file"


def test_deleting_and_recreating_a_name_in_one_save(store, tmp_path, monkeypatch):
    from venus_profile.macro_library import MacroLibrary

    monkeypatch.setattr(MacroLibrary, "trash", staticmethod(lambda path: path.rename(tmp_path / path.name)))
    workspace = VenusWorkspace.open(store)
    first = workspace.library.create("Same")
    workspace.save_all()
    workspace.delete_macro(first.macro_id)
    workspace.library.create("Same")
    workspace.save_all()
    assert (store.macro_library_dir / "Same.MSMACRO").exists()


def test_old_app_store_library_is_migrated_once(store, project_root):
    import json

    data = macro_fixture(project_root, "POE-Flasks.MSMACRO").read_bytes()
    store.library_dir.mkdir(parents=True)
    (store.library_dir / "abc123.MSMACRO").write_bytes(data)
    store.library_index_path.write_text(json.dumps(
        {"version": 1, "macros": [{"id": "abc123", "name": "POE-Flasks", "source": ""}]}))
    slots = [None] * 15
    slots[3] = "abc123"
    store.bank_path.write_text(json.dumps({"version": 1, "slots": slots}))
    workspace = VenusWorkspace.open(store)
    assert [item.name for item in workspace.library.entries] == ["POE-Flasks"]
    assert workspace.library.slot_macro(3).name == "POE-Flasks"
    assert (store.macro_library_dir / "POE-Flasks.MSMACRO").read_bytes() == data
    assert (store.library_dir / "abc123.MSMACRO").exists()  # old copy kept
    assert not store.library_index_path.exists()
    assert store.library_index_path.with_name("library.json.migrated").exists()
