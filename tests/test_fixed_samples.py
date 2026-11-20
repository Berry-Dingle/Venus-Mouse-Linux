from pathlib import Path

import pytest

from venus_profile.actions import ActionRecord, ActionType
from venus_profile.macro import MacroFile
from venus_profile.macroset import MacroSet
from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_macro, validate_pfd


FIXED_NAMES = [
    "04_Key1_Repeat3R.pfd",
    "05_Key1_Repeat3_5sR.MSMACRO",
    "06_Key1_WhileHeld_5sR.pfd",
    "08_Keys12_DefDelayR.pfd",
    "09_Keys12_RecDelayR.pfd",
    "10_Keys12_NoDelayR.MSMACRO",
    "NM00_Base.pfd",
]


@pytest.fixture(scope="module")
def fixed(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "Fixed"


@pytest.mark.parametrize("name", FIXED_NAMES)
def test_fixed_file_round_trips_byte_exact(fixed, name):
    path = fixed / name
    if path.suffix.lower() == ".pfd":
        model = PfdProfile.from_path(path)
    else:
        model = MacroFile.from_path(path)
    assert model.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    ("name", "slot", "macro_name"),
    [
        ("04_Key1_Repeat3R.pfd", 3, "04_Key1_Repeat3"),
        ("06_Key1_WhileHeld_5sR.pfd", 5, "06_Key1_WhileHeld_5s"),
        ("08_Keys12_DefDelayR.pfd", 7, "08_Keys12_DefDelay"),
        ("09_Keys12_RecDelayR.pfd", 8, "09_Keys12_RecDelay"),
    ],
)
def test_fixed_macro_pfd_is_complete_and_assignment_valid(
        project_root, fixed, name, slot, macro_name):
    controlled = project_root / "ProfilesMacros" / "TestProfiles"
    profile = PfdProfile.from_path(fixed / name)
    macro_set = MacroSet.from_path(controlled / "MacroDB" / "MacroSet.MSDB")
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert validate_pfd(profile, macro_set=macro_set,
                        macro_dir=controlled / "MacroDB") == []
    assert len(profile.macro_assignments) == 1
    assignment = profile.macro_assignments[0]
    assert assignment.section == "ButtonAssigned_7"
    assert assignment.key_number == slot
    assert assignment.macro_name == macro_name


@pytest.mark.parametrize(
    "name",
    ["05_Key1_Repeat3_5sR.MSMACRO", "10_Keys12_NoDelayR.MSMACRO"],
)
def test_fixed_macro_file_is_complete_and_valid(fixed, name):
    macro = MacroFile.from_path(fixed / name)
    assert macro.has_action_blob
    assert len(macro.action_struct.data) == 189
    assert macro.action_struct.valid
    assert validate_macro(macro) == []


def test_fixed_explicit_5000_ms_macro(fixed):
    macro = MacroFile.from_path(fixed / "05_Key1_Repeat3_5sR.MSMACRO")
    assert macro.count == 3
    assert macro.loop_type == 0
    assert macro.loop_time == 3
    assert [record.raw for record in macro.records] == [
        bytes.fromhex("841E00"),
        bytes.fromhex("041E00"),
        bytes.fromhex("06F401"),
    ]
    assert macro.records[2].delay_ms == 5000


def test_fixed_no_auto_delay_editor_output(fixed):
    macro = MacroFile.from_path(fixed / "10_Keys12_NoDelayR.MSMACRO")
    assert macro.delay_type == 2
    assert macro.count == 7
    assert [record.raw for record in macro.records] == [
        bytes.fromhex(value) for value in (
            "841E00", "060100", "041E00", "060100",
            "841F00", "060100", "041F00",
        )
    ]
    assert sum(record.delay_ms for record in macro.records
               if record.action_type == ActionType.DELAY) == 30


def test_default_500_encoding_is_distinct_from_explicit_5000():
    default_500 = ActionRecord.delay(500)
    explicit_5000 = ActionRecord.delay(5000)
    assert default_500.raw == bytes.fromhex("063200")
    assert explicit_5000.raw == bytes.fromhex("06F401")
    assert default_500.raw != explicit_5000.raw


def test_recorded_and_no_auto_delay_modes_are_distinct(project_root, fixed):
    recorded = MacroFile.from_path(
        project_root / "ProfilesMacros" / "TestProfiles" /
        "MacroDB" / "09_Keys12_RecDelay.MSMACRO")
    no_auto = MacroFile.from_path(fixed / "10_Keys12_NoDelayR.MSMACRO")
    assert recorded.delay_type == 0
    assert no_auto.delay_type == 2
    assert [record.delay_ms for record in recorded.records
            if record.action_type == ActionType.DELAY] == [90, 860, 70]
    assert [record.delay_ms for record in no_auto.records
            if record.action_type == ActionType.DELAY] == [10, 10, 10]


def test_fixed_and_while_held_modes_are_distinct(project_root, fixed):
    controlled = project_root / "ProfilesMacros" / "TestProfiles" / "MacroDB"
    fixed_profile = PfdProfile.from_path(fixed / "04_Key1_Repeat3R.pfd")
    held_profile = PfdProfile.from_path(fixed / "06_Key1_WhileHeld_5sR.pfd")
    fixed_macro = MacroFile.from_path(controlled / "04_Key1_Repeat3.MSMACRO")
    held_macro = MacroFile.from_path(controlled / "06_Key1_WhileHeld_5s.MSMACRO")
    assert fixed_profile.macro_assignments[0].key_number == 3
    assert held_profile.macro_assignments[0].key_number == 5
    assert (fixed_macro.loop_type, fixed_macro.loop_time) == (0, 3)
    assert (held_macro.loop_type, held_macro.loop_time) == (1, 1)


def test_nm00_base_is_approved_complete_baseline(fixed):
    profile = PfdProfile.from_path(fixed / "NM00_Base.pfd")
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert profile.dpi_stage_struct is not None
    assert profile.dpi_stage_struct.valid
    assert len(profile.dpi_stage_struct.data) == 408
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert all(not button.macro_name for button in profile.buttons)
    functions = [button.function for button in profile.buttons]
    assert functions == [
        1, 2, 3, 12, 48, 49,
        6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
        52, 53,
    ]
    for button in profile.buttons:
        assert button.blob is not None
        assert len(button.blob.data) == 189
        assert button.blob.valid
    assert validate_pfd(profile) == []


@pytest.mark.parametrize(
    ("relative", "expected_code"),
    [
        ("04_Key1_Repeat3.pfd", "button-count"),
        ("06_Key1_WhileHeld_5s.pfd", "button-count"),
        ("08_Keys12_DefDelay.pfd", "button-count"),
        ("09_Keys12_RecDelay.pfd", "button-count"),
    ],
)
def test_original_malformed_pfds_remain_negative(
        project_root, relative, expected_code):
    path = project_root / "ProfilesMacros" / "TestProfiles" / relative
    issues = validate_pfd(PfdProfile.from_path(path))
    assert expected_code in [issue.code for issue in issues]


@pytest.mark.parametrize(
    "name",
    ["05_Key1_Repeat3_5s.MSMACRO", "10_Keys12_NoDelay.MSMACRO"],
)
def test_original_malformed_macros_remain_negative(project_root, name):
    path = (project_root / "ProfilesMacros" / "TestProfiles" /
            "MacroDB" / name)
    issues = validate_macro(MacroFile.from_path(path))
    assert "missing-field" in [issue.code for issue in issues]
