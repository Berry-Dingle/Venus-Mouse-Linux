"""PFD button-function catalogue: decoding, editing, preservation."""

import pytest

from venus_profile import button_functions as bf
from venus_profile.button_assignments import (
    CONFIRMED_ACTION_LABELS,
    HISTORICAL_ACTION_LABELS,
    label_matches_record,
)
from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_pfd


def factory(project_root, number=1):
    return PfdProfile.from_path(
        project_root / "investigation" / "extracted" / "userdocs"
        / "UtechSmart Venus Gaming Mouse" / f"RSProfile{number}.pfd"
    )


def fixture(project_root, name):
    return PfdProfile.from_path(project_root / "ProfilesMacros" / "NonMacroTestsII" / name)


def test_catalogue_matches_every_confirmed_device_record():
    # Every simple function template that is also a confirmed four-byte record
    # must carry the same meaning as the decoder allowlist.
    expected = {
        1: "Left Click", 2: "Right Click", 3: "Middle Click", 4: "Forward", 5: "Backward",
        47: "DPI Switch Cycle", 48: "DPI+", 49: "DPI-", 55: "Profile Switch",
        56: "Profile+", 57: "Profile-", 50: "Report Rate+", 51: "Report Rate-", 16: "Disable",
    }
    for code, label in expected.items():
        template = bf.FUNCTIONS_BY_CODE[code].device_template
        assert CONFIRMED_ACTION_LABELS[template] == label


def test_catalogue_codes_are_unique_and_categorised():
    codes = [item.code for item in bf.FUNCTIONS]
    assert len(codes) == len(set(codes)) == 51
    assert all(item.category in bf.CATEGORY_ORDER for item in bf.FUNCTIONS)
    assert 13 not in bf.FUNCTIONS_BY_CODE  # present in hid.exe but never labelled


def test_factory_profile_decodes_to_mmo_layout(project_root):
    views = bf.read_all_assignments(factory(project_root))
    assert [view.function_code for view in views[:6]] == [1, 2, 3, 12, 48, 49]
    assert [view.single_key for view in views[6:18]] == [
        0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x57, 0x56,
    ]
    assert views[6].label == "Key 1"
    assert views[16].label == "Key Keypad +"
    assert views[18].label == "Scroll Up" and views[19].label == "Scroll Down"
    # Derived records agree with saved factory device memory.
    assert views[6].device_record == bytes.fromhex("90 00 1E 00")
    assert views[16].device_record == bytes.fromhex("90 00 57 00")
    assert views[3].device_record == bytes.fromhex("99 81 03 00")
    assert views[18].device_record == bytes.fromhex("8B 00 00 00")


def test_dayz_disabled_buttons_keep_their_stale_key_bytes(project_root):
    dayz = PfdProfile.from_path(project_root / "investigation" / "custom-profiles" / "DayZ.pfd")
    view = bf.read_assignment(dayz, 13)
    assert view.function_code == bf.FUNCTION_DISABLE
    assert view.label == "Disable"
    assert view.blob[0] == 0x2C  # preserved, not interpreted
    assert bf.read_assignment(dayz, 7).label == "Key F"


@pytest.mark.parametrize("name, setting, record", [
    ("01_ComboShiftWin.pfd", bf.ComboSetting((0xE1, 0xE3), None), "8F 0A 00 00"),
    ("02_ComboShiftA.pfd", bf.ComboSetting((0xE1,), 0x04), "8F 02 04 00"),
])
def test_combo_decoding_and_encoding_match_windows_fixtures(project_root, name, setting, record):
    profile = fixture(project_root, name)
    view = bf.read_assignment(profile, 7)
    assert view.combo == setting
    assert view.device_record == bytes.fromhex(record)
    data, count = bf.encode_combo(setting)
    assert count == view.key_number
    assert view.blob.startswith(data) and not any(view.blob[len(data):])


@pytest.mark.parametrize("name, record", [
    ("00_Fire_Default.pfd", "99 81 03 00"),
    ("02_Fire_Delay20.pfd", "99 81 03 02"),
    ("03_Fire_Right.pfd", "99 82 03 01"),
    ("04_Fire_Middle.pfd", "99 84 03 01"),
    ("05_Fire_KeyA.pfd", "99 04 03 01"),
])
def test_fire_key_records_match_confirmed_campaign(project_root, name, record):
    view = bf.read_assignment(fixture(project_root, name), 4)
    assert view.device_record == bytes.fromhex(record)
    assert CONFIRMED_ACTION_LABELS[bytes.fromhex(record)].startswith("Fire Key")


def test_setters_change_only_the_used_fields(project_root):
    profile = factory(project_root)
    before = profile.to_bytes()
    bf.set_single_key(profile, 7, 0x1E)  # identical value: no change at all
    assert profile.to_bytes() == before
    bf.set_simple_function(profile, 8, bf.FUNCTION_DISABLE)
    after = profile.to_bytes()
    changed = [(a, b) for a, b in zip(before.split(b"\r\n"), after.split(b"\r\n")) if a != b]
    assert changed == [(b"ButtonFunc=6", b"ButtonFunc=16")]
    # The stale key byte is preserved exactly like the Windows editor does.
    assert bf.read_assignment(profile, 8).blob[0] == 0x1F
    assert not [issue for issue in validate_pfd(profile) if issue.severity == "error"]


def test_single_key_combo_fire_and_macro_round_trip(project_root):
    profile = factory(project_root)
    bf.set_single_key(profile, 9, 0x2C)
    bf.set_combo(profile, 10, bf.ComboSetting((0xE0, 0xE1), 0x04))
    bf.set_fire_key(profile, 4, bf.FIRE_TRIGGER_RIGHT, 30)
    bf.set_macro(profile, 11, 3, "Rotation")
    reloaded = PfdProfile.from_bytes(profile.to_bytes())
    assert bf.read_assignment(reloaded, 9).label == "Key Space"
    assert bf.read_assignment(reloaded, 10).device_record == bytes.fromhex("8F 03 04 00")
    fire = bf.read_assignment(reloaded, 4)
    assert fire.device_record == bytes.fromhex("99 82 03 03")
    macro = bf.read_assignment(reloaded, 11)
    assert (macro.function_code, macro.key_number, macro.macro_name) == (100, 3, "Rotation")
    bf.set_single_key(reloaded, 11, 0x05)
    assert bf.read_assignment(reloaded, 11).macro_name == ""
    assert not [issue for issue in validate_pfd(reloaded) if issue.severity == "error"]


def test_fire_count_is_kept_in_the_button_structure_behind_a_marker(project_root):
    profile = factory(project_root)
    bf.set_fire_key(profile, 4, bf.FIRE_TRIGGER_LEFT, 20, 7)
    reloaded = PfdProfile.from_bytes(profile.to_bytes())          # survives Save/Load
    view = bf.read_assignment(reloaded, 4)
    assert view.fire.count == 7 and view.blob[:4] == bytes((bf.FIRE_TRIGGER_LEFT,)) + b"VC" + bytes((7,))
    assert view.device_record == bytes.fromhex("99 81 07 02")
    assert not [issue for issue in validate_pfd(reloaded) if issue.severity == "error"]
    bf.set_fire_key(reloaded, 4, bf.FIRE_TRIGGER_LEFT, 20)        # no count: back to a Windows-style blob
    view = bf.read_assignment(reloaded, 4)
    assert view.fire.count is None and view.blob[1:] == bytes(len(view.blob) - 1)
    assert view.device_record == bytes.fromhex("99 81 03 02")     # Windows' default count


def test_stale_bytes_after_a_fire_trigger_are_not_a_count(project_root):
    profile = factory(project_root)
    bf.set_combo(profile, 4, bf.ComboSetting((0xE0,), 0x04))      # leaves (usage, 05, 80) triples behind
    bf._set_field(profile, 4, "ButtonFunc", str(bf.FUNCTION_FIRE_KEY))   # as Windows does: blob kept
    assert bf.read_assignment(profile, 4).fire.count is None


@pytest.mark.parametrize("count", (0, 256, -1, True, 3.0))
def test_bad_fire_counts_are_rejected(project_root, count):
    with pytest.raises(ValueError):
        bf.set_fire_key(factory(project_root), 4, bf.FIRE_TRIGGER_LEFT, 10, count)


def test_invalid_edits_are_rejected():
    with pytest.raises(ValueError):
        bf.encode_combo(bf.ComboSetting((), 0x04))
    with pytest.raises(ValueError):
        bf.encode_combo(bf.ComboSetting((0xE5,), 0x04))
    with pytest.raises(ValueError):
        bf.encode_combo(bf.ComboSetting((0xE0,), 0xE1))


def test_unknown_function_is_reported_and_preserved(project_root):
    profile = factory(project_root)
    section = bf.button_section(profile, 9)
    profile.document.set(section, "ButtonFunc", "77")
    view = bf.read_assignment(profile, 9)
    assert not view.known
    assert view.label == "Unknown function 77 (preserved)"
    assert view.device_record is None
    assert b"ButtonFunc=77" in profile.to_bytes()


def test_left_click_retention_rule(project_root):
    profile = factory(project_root)
    assert bf.left_click_retained(profile)
    bf.set_simple_function(profile, 1, bf.FUNCTION_DISABLE)
    assert not bf.left_click_retained(profile)


def test_corrected_label_and_historical_alias():
    raw = bytes.fromhex("90 00 1E 00")
    assert CONFIRMED_ACTION_LABELS[raw] == "Single Key 1"
    assert label_matches_record(raw, "Single Key 1")
    assert label_matches_record(raw, "Single Key A")
    assert not label_matches_record(bytes.fromhex("90 00 04 00"), "Single Key 1")
    assert HISTORICAL_ACTION_LABELS == {raw: ("Single Key A",)}
