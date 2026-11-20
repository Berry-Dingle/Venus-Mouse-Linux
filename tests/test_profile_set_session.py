import pytest

from venus_profile.profile_session import (
    AmbiguousProfileError,
    DirtyProfileReplacementError,
    IncompatibleProfileError,
    MissingProfileError,
    ProfileLoadError,
    ProfileSaveError,
    ProfileSetSession,
)
from venus_profile.pfd import PfdProfile, PollingRateHz


def official_profile(project_root, number):
    return (
        project_root
        / "investigation"
        / "extracted"
        / "userdocs"
        / "UtechSmart Venus Gaming Mouse"
        / f"RSProfile{number}.pfd"
    )


def historical_profile(project_root, name="00_Baseline_NoMacro.pfd"):
    return project_root / "ProfilesMacros" / "TestProfiles" / name


def edit_profile(session):
    current = session.profile.acceleration
    session.profile.set_acceleration(1 if current != 1 else 2)


def test_five_slots_load_independent_numbered_documents(project_root):
    profiles = ProfileSetSession(selected_slot=3)

    for slot in range(1, 6):
        profiles.select_slot(slot)
        loaded = profiles.load_into_selected(official_profile(project_root, slot))
        assert loaded.profile.group_section == f"GROUP{slot - 1}"
        assert profiles.selected_slot == slot

    assert profiles.loaded_slots == (1, 2, 3, 4, 5)
    assert tuple(profiles.slots) == (1, 2, 3, 4, 5)


def test_selection_is_independent_of_loaded_slots():
    profiles = ProfileSetSession(selected_slot=4)

    assert profiles.selected_slot == 4
    assert profiles.selected_session is None
    assert profiles.select_slot(2) is None
    assert profiles.selected_slot == 2
    assert profiles.loaded_slots == ()


@pytest.mark.parametrize("source_slot", range(1, 6))
@pytest.mark.parametrize("destination_slot", range(1, 6))
def test_any_factory_profile_imports_into_any_gui_slot(
    project_root, source_slot, destination_slot
):
    source = official_profile(project_root, source_slot)
    original = source.read_bytes()
    profiles = ProfileSetSession(selected_slot=destination_slot)
    loaded = profiles.import_into_selected(source)

    assert loaded.profile.group_section == f"GROUP{destination_slot - 1}"
    assert profiles.selected_session is loaded
    assert len(loaded.profile.buttons) == 20
    assert loaded.profile.document.get(
        f"GROUP{destination_slot - 1}", "GroupID"
    ) == "0"
    assert loaded.profile.document.get(
        f"GROUP{destination_slot - 1}", "Group"
    ) == PfdProfile.from_path(source).document.get(
        f"GROUP{source_slot - 1}", "Group"
    )
    imported = loaded.profile.to_bytes()
    if source_slot == destination_slot:
        assert imported == original
    else:
        normalized = imported.replace(
            f"[GROUP{destination_slot - 1}]".encode(),
            f"[GROUP{source_slot - 1}]".encode(),
        ).replace(
            f"ButtonAssigned{destination_slot - 1}_".encode(),
            f"ButtonAssigned{source_slot - 1}_".encode(),
        )
        assert normalized == original
    assert source.read_bytes() == original


def test_selection_and_dirty_state_are_independent_per_slot(project_root):
    profiles = ProfileSetSession()
    profiles.load_into_selected(official_profile(project_root, 1))
    first = profiles.selected_session
    edit_profile(first)

    profiles.select_slot(2)
    profiles.load_into_selected(official_profile(project_root, 2))
    second = profiles.selected_session
    edit_profile(second)

    assert profiles.dirty_slots == (1, 2)
    assert profiles.dirty
    profiles.select_slot(1)
    assert profiles.selected_session is first
    assert first.dirty
    assert second.dirty
    assert profiles.dirty_slots == (1, 2)


def test_dirty_slot_cannot_be_replaced_without_explicit_confirmation(project_root):
    profiles = ProfileSetSession()
    first = profiles.load_into_selected(official_profile(project_root, 1))
    edit_profile(first)

    with pytest.raises(DirtyProfileReplacementError, match="explicit replacement"):
        profiles.load_into_selected(
            official_profile(project_root, 1), confirm_replace_dirty=1
        )
    assert profiles.selected_session is first
    assert first.dirty

    replacement = profiles.load_into_selected(
        official_profile(project_root, 1), confirm_replace_dirty=True
    )
    assert replacement is not first
    assert not replacement.dirty


def test_failed_replacement_does_not_discard_dirty_session(project_root, tmp_path):
    profiles = ProfileSetSession()
    first = profiles.load_into_selected(official_profile(project_root, 1))
    edit_profile(first)

    malformed = official_profile(project_root, 2).read_bytes().replace(
        b"[GROUP1]", b"[GROUP6]", 1
    )
    bad_path = tmp_path / "unsupported-group.pfd"
    bad_path.write_bytes(malformed)
    with pytest.raises(IncompatibleProfileError):
        profiles.load_into_selected(
            bad_path, confirm_replace_dirty=True
        )
    assert profiles.selected_session is first
    assert first.dirty


def test_save_as_saves_only_selected_slot(project_root, tmp_path):
    profiles = ProfileSetSession()
    first = profiles.load_into_selected(official_profile(project_root, 1))
    edit_profile(first)
    profiles.select_slot(2)
    second = profiles.load_into_selected(official_profile(project_root, 2))
    second.profile.set_polling_rate_hz(PollingRateHz.HZ_1000)
    first_bytes = first.profile.to_bytes()
    destination = tmp_path / "slot-2-copy.pfd"

    assert profiles.save_as(destination) == destination
    assert destination.read_bytes() == second.profile.to_bytes()
    assert second.path == destination
    assert not second.dirty
    assert first.dirty
    assert first.profile.to_bytes() == first_bytes
    assert profiles.dirty_slots == (1,)


def test_cross_slot_save_as_exports_destination_identity_and_reloads(
    project_root, tmp_path
):
    source = official_profile(project_root, 1)
    source_bytes = source.read_bytes()
    profiles = ProfileSetSession(selected_slot=4)
    imported = profiles.import_into_selected(source)
    destination = tmp_path / "profile-4-export.pfd"

    assert profiles.save_as(destination) == destination
    assert source.read_bytes() == source_bytes
    assert b"[GROUP3]" in destination.read_bytes()
    assert b"[ButtonAssigned3_20]" in destination.read_bytes()

    reopened = ProfileSetSession(selected_slot=4).load_into_selected(destination)
    assert reopened.profile.to_bytes() == destination.read_bytes()
    assert reopened.profile.group_section == "GROUP3"


@pytest.mark.parametrize("slot", range(1, 6))
def test_portable_export_from_each_slot_is_unsuffixed_and_session_unchanged(
    project_root, tmp_path, slot
):
    source = official_profile(project_root, slot)
    source_bytes = source.read_bytes()
    profiles = ProfileSetSession(selected_slot=slot)
    session = profiles.import_into_selected(source)
    session.profile.set_acceleration(
        1 if session.profile.acceleration != 1 else 2
    )
    before_bytes = session.profile.to_bytes()
    before_path = session.path
    before_saved = session._saved_bytes
    destination = tmp_path / f"portable-{slot}.pfd"

    assert profiles.export_copy(destination) == destination
    exported = destination.read_bytes()
    assert b"[GROUP]" in exported
    assert b"[GROUP0]" not in exported
    assert b"[ButtonAssigned_1]" in exported
    assert b"[ButtonAssigned_20]" in exported
    assert b"ButtonAssigned_1=" in exported
    assert b"ButtonAssigned_20=" in exported
    assert b"[ButtonAssigned0_1]" not in exported
    assert session.profile.group_section == f"GROUP{slot - 1}"
    assert session.profile.to_bytes() == before_bytes
    assert session.path == before_path
    assert session._saved_bytes == before_saved
    assert session.dirty and profiles.dirty_slots == (slot,)
    assert source.read_bytes() == source_bytes


def test_portable_export_imports_into_another_slot_and_preserves_macro_opaque_data(
    project_root, tmp_path
):
    source = project_root / "ProfilesMacros" / "POE_Macro.pfd"
    profiles = ProfileSetSession(selected_slot=1)
    original = profiles.import_into_selected(source)
    macro_before = [(b.key_number, b.macro_name, b.loop_number,
                     b.blob.data if b.blob else None)
                    for b in original.profile.macro_assignments]
    unknown = b"UnrecognizedOpaque=preserve-me"
    # Add a valid unknown scalar while keeping the original source untouched.
    original.profile.document.set("GROUP0", "Group", "0")
    group_header = next(i for i, line in enumerate(original.profile.document.lines)
                        if line.raw.strip() == "[GROUP0]")
    original.profile.document.lines.insert(
        group_header + 1,
        type(original.profile.document.lines[group_header + 1])(
            raw=unknown.decode(), section="GROUP0", key="UnrecognizedOpaque",
            value="preserve-me"
        )
    )
    original.profile.document._rebuild_index()
    portable_path = tmp_path / "portable-macro.pfd"
    profiles.export_copy(portable_path)
    portable = portable_path.read_bytes()
    assert unknown in portable

    imported = ProfileSetSession(selected_slot=5).import_into_selected(portable_path)
    macro_after = [(b.key_number, b.macro_name, b.loop_number,
                    b.blob.data if b.blob else None)
                   for b in imported.profile.macro_assignments]
    assert macro_after == macro_before
    assert imported.profile.document.get("GROUP4", "UnrecognizedOpaque") == "preserve-me"


def test_portable_export_matches_genuine_dayz_structural_identity(project_root, tmp_path):
    source = project_root / "investigation" / "custom-profiles" / "DayZ.pfd"
    profiles = ProfileSetSession(selected_slot=3)
    profiles.import_into_selected(source)
    destination = tmp_path / "DayZ-portable.pfd"
    profiles.export_copy(destination)
    exported = destination.read_bytes()
    assert exported == source.read_bytes()
    assert b"[GROUP]" in exported
    assert b"[ButtonAssigned_1]" in exported
    assert b"ButtonAssigned_1=" in exported
    assert b"Group=0" in exported
    assert b"GroupID=" not in exported


def test_slot_specific_export_copy_keeps_identity_and_dirty_state(project_root, tmp_path):
    profiles = ProfileSetSession(selected_slot=4)
    session = profiles.import_into_selected(official_profile(project_root, 1))
    session.profile.set_acceleration(
        1 if session.profile.acceleration != 1 else 2
    )
    before = (session.profile.to_bytes(), session.path, session._saved_bytes)
    destination = tmp_path / "slot-copy.pfd"
    profiles.export_copy(destination, portable=False)
    assert b"[GROUP3]" in destination.read_bytes()
    assert b"[ButtonAssigned3_20]" in destination.read_bytes()
    reopened = ProfileSetSession(selected_slot=2).import_into_selected(destination)
    assert reopened.profile.group_section == "GROUP1"
    assert b"[GROUP1]" in reopened.profile.to_bytes()
    assert (session.profile.to_bytes(), session.path, session._saved_bytes) == before
    assert session.dirty and profiles.dirty_slots == (4,)


def test_export_no_overwrite_and_malformed_identity_rejection(project_root, tmp_path):
    profiles = ProfileSetSession()
    session = profiles.import_into_selected(official_profile(project_root, 1))
    existing = tmp_path / "keep.pfd"
    existing.write_bytes(b"keep")
    with pytest.raises(ProfileSaveError, match="already exists"):
        profiles.export_copy(existing)
    assert existing.read_bytes() == b"keep"

    session.profile.document.rename_section("ButtonAssigned0_1", "ButtonAssigned1_1")
    with pytest.raises(IncompatibleProfileError, match="identities"):
        profiles.export_copy(tmp_path / "bad.pfd")

    session.profile.document.rename_section("ButtonAssigned1_1", "ButtonAssigned0_1")
    session.profile.document.lines.append(
        type(session.profile.document.lines[0])(raw="[GROUP1]", section="GROUP1")
    )
    session.profile.document._rebuild_index()
    with pytest.raises(AmbiguousProfileError, match="GROUP sections"):
        profiles.export_copy(tmp_path / "ambiguous.pfd")


def test_historical_legacy_and_macro_profiles_remap_without_losing_data(
    project_root
):
    for filename in ("00_Baseline_NoMacro.pfd", "POE_Macro.pfd"):
        source = (historical_profile(project_root, filename)
                 if filename != "POE_Macro.pfd"
                 else project_root / "ProfilesMacros" / filename)
        original = source.read_bytes()
        before = PfdProfile.from_path(source)
        profiles = ProfileSetSession(selected_slot=4)
        imported = profiles.import_into_selected(source)

        assert imported.profile.group_section == "GROUP3"
        assert len(imported.profile.buttons) == 20
        assert imported.profile.document.get("GROUP3", "Group") == before.document.get(
            "GROUP", "Group"
        )
        assert imported.profile.to_bytes().replace(
            b"[GROUP3]", b"[GROUP]"
        ).replace(b"ButtonAssigned3_", b"ButtonAssigned_") == original
        assert source.read_bytes() == original
        assert [
            (button.button_id, button.function, button.key_number,
             button.macro_name, button.loop_number,
             button.blob.data if button.blob else None)
            for button in imported.profile.buttons
        ] == [
            (button.button_id, button.function, button.key_number,
             button.macro_name, button.loop_number,
             button.blob.data if button.blob else None)
            for button in before.buttons
        ]

    poe = ProfileSetSession(selected_slot=4).import_into_selected(
        project_root / "ProfilesMacros" / "POE_Macro.pfd"
    )
    assert [(b.key_number, b.macro_name) for b in poe.profile.macro_assignments] == [
        (b.key_number, b.macro_name)
        for b in PfdProfile.from_path(project_root / "ProfilesMacros" / "POE_Macro.pfd").macro_assignments
    ]


@pytest.mark.parametrize("destination_slot", range(1, 6))
def test_genuine_dayz_legacy_profile_imports_into_any_slot_without_rewriting_source(
    project_root, tmp_path, destination_slot
):
    source = project_root / "investigation" / "custom-profiles" / "DayZ.pfd"
    original = source.read_bytes()
    profiles = ProfileSetSession(selected_slot=destination_slot)

    imported = profiles.import_into_selected(source)

    target_group = f"GROUP{destination_slot - 1}"
    expected = (original.replace(b"[GROUP]", f"[{target_group}]".encode())
                .replace(b"ButtonAssigned_",
                         f"ButtonAssigned{destination_slot - 1}_".encode()))
    assert imported.profile.group_section == target_group
    assert len(imported.profile.buttons) == 20
    assert imported.profile.document.get(target_group, "Group") == "0"
    assert imported.profile.document.get(target_group, "GroupID") is None
    assert imported.profile.to_bytes() == expected
    assert source.read_bytes() == original

    destination = tmp_path / "dayz-profile-copy.pfd"
    assert profiles.save_as(destination) == destination
    assert destination.read_bytes() == expected


def test_unknown_data_and_original_formatting_survive_slot_edit(project_root):
    source = official_profile(project_root, 1)
    original = source.read_bytes()
    profiles = ProfileSetSession()
    session = profiles.load_into_selected(source)
    session.profile.set_polling_rate_hz(PollingRateHz.HZ_1000)
    edited = session.profile.to_bytes()

    expected = original.replace(b"PollingRate=2", b"PollingRate=1", 1)
    assert edited == expected
    assert b"\r\n" in edited
    assert session.profile.dpi_stage_struct.encode().encode("ascii") in edited


def test_missing_invalid_and_ambiguous_profile_structures(project_root, tmp_path):
    profiles = ProfileSetSession()
    with pytest.raises(MissingProfileError, match="missing or not a file"):
        profiles.load_into_selected(tmp_path / "missing.pfd")

    invalid = tmp_path / "invalid.pfd"
    invalid.write_bytes(
        official_profile(project_root, 1).read_bytes().replace(b"PollingRate=2\r\n", b"", 1)
    )
    with pytest.raises(ProfileLoadError, match="Invalid PFD"):
        profiles.load_into_selected(invalid)

    missing_button = tmp_path / "missing-button.pfd"
    missing_button.write_bytes(
        official_profile(project_root, 1).read_bytes().split(
            b"[ButtonAssigned0_20]", 1
        )[0]
    )
    with pytest.raises(MissingProfileError, match="missing numbered button"):
        profiles.load_into_selected(missing_button)

    ambiguous = tmp_path / "ambiguous.pfd"
    ambiguous.write_bytes(
        official_profile(project_root, 1).read_bytes()
        + b"\r\n[GROUP1]\r\nUnrelated=1\r\n"
    )
    with pytest.raises(AmbiguousProfileError, match="GROUP sections"):
        profiles.load_into_selected(ambiguous)

    mismatched = tmp_path / "mismatched-buttons.pfd"
    mismatched.write_bytes(
        official_profile(project_root, 1).read_bytes().replace(
            b"[ButtonAssigned0_1]", b"[ButtonAssigned1_1]", 1
        )
    )
    with pytest.raises(IncompatibleProfileError, match="identities"):
        profiles.load_into_selected(mismatched)

    invalid_reference = tmp_path / "invalid-reference.pfd"
    invalid_reference.write_bytes(
        official_profile(project_root, 1).read_bytes().replace(
            b"ButtonAssigned0_1=", b"ButtonAssigned6_1=", 1
        )
    )
    with pytest.raises(IncompatibleProfileError, match="references"):
        profiles.load_into_selected(invalid_reference)

    noncanonical = tmp_path / "noncanonical-button.pfd"
    noncanonical.write_bytes(
        official_profile(project_root, 1).read_bytes().replace(
            b"[ButtonAssigned0_1]", b"[ButtonAssigned0_01]", 1
        )
    )
    with pytest.raises(IncompatibleProfileError, match="non-canonical"):
        profiles.load_into_selected(noncanonical)
