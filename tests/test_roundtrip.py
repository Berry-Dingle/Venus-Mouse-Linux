from pathlib import Path

import pytest

from venus_profile.macro import MacroFile
from venus_profile.macroset import MacroSet
from venus_profile.pfd import PfdProfile
from venus_profile.validation import detect_kind


def all_samples(controlled: Path) -> list[Path]:
    return sorted(
        path for path in controlled.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pfd", ".msdb", ".msmacro"}
    )


def test_controlled_inventory_count(controlled):
    assert len(all_samples(controlled)) == 44


def test_every_controlled_file_exact_round_trip(controlled):
    for path in all_samples(controlled):
        model = detect_kind(path)
        assert model.to_bytes() == path.read_bytes(), path


def test_complete_macro_models_have_expected_dimensions(controlled):
    complete = 0
    incomplete = []
    for path in sorted(controlled.rglob("*.MS*")):
        if path.name == "MacroSet.MSDB":
            continue
        macro = MacroFile.from_path(path)
        if not macro.has_action_blob:
            incomplete.append(path.name)
            continue
        complete += 1
        assert len(macro.action_struct.data) == 189
        assert len(macro.all_records) == 63
        assert macro.action_struct.valid
    assert complete == 26
    assert sorted(incomplete) == [
        "05_Key1_Repeat3_5s.MSMACRO",
        "10_Keys12_NoDelay.MSMACRO",
    ]


def test_pfd_truncations_are_preserved(controlled):
    expected_sections = {
        "04_Key1_Repeat3.pfd": 4,
        "06_Key1_WhileHeld_5s.pfd": 14,
        "08_Keys12_DefDelay.pfd": 4,
        "09_Keys12_RecDelay.pfd": 20,
    }
    for name, count in expected_sections.items():
        profile = PfdProfile.from_path(controlled / name)
        assert len(profile.document.sections) == count
        assert profile.to_bytes() == (controlled / name).read_bytes()


def test_macroset_exact_round_trip_and_slots(controlled):
    path = controlled / "MacroDB" / "MacroSet.MSDB"
    macro_set = MacroSet.from_path(path)
    assert len(macro_set.names) == 15
    assert macro_set.names[0] == "01_Key1_Once"
    assert macro_set.names[13] == "15_Key1_LClick_Key2"
    assert macro_set.names[14] == ""
    assert macro_set.to_bytes() == path.read_bytes()


def test_pfd_macro_assignment_is_in_memory_only(controlled):
    path = controlled / "00_Baseline_NoMacro.pfd"
    original = path.read_bytes()
    profile = PfdProfile.from_bytes(original)
    profile.assign_macro("ButtonAssigned_7", slot=3, name="Example")
    encoded = profile.to_bytes()
    assert b"ButtonFunc=100" in encoded
    assert b"KeyNumber=3" in encoded
    assert b"MacroName=Example" in encoded
    assert path.read_bytes() == original


def test_macroset_edit_is_in_memory_only(controlled):
    path = controlled / "MacroDB" / "MacroSet.MSDB"
    original = path.read_bytes()
    macro_set = MacroSet.from_bytes(original)
    macro_set.set_name(14, "Example")
    assert macro_set.names[14] == "Example"
    assert path.read_bytes() == original
