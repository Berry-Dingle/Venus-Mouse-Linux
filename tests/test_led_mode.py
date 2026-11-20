from pathlib import Path

import pytest

from venus_profile.pfd import (
    BreathingSpeed,
    BrightnessLevel,
    LedMode,
    PfdProfile,
)
from venus_profile.validation import validate_pfd


CASES = [
    ("NM16_ModeBreathing.pfd", 0, LedMode.BREATHING),
    ("NM17_ModeFull.pfd", 1, LedMode.FULL_BRIGHTNESS),
    ("NM18_ModeRainbow.pfd", 2, LedMode.RAINBOW),
    ("NM19_ModeWave.pfd", 3, LedMode.WAVE),
    ("NM20_ModeFlash.pfd", 4, LedMode.FLASH),
    ("NM21_ModeOff.pfd", 5, LedMode.OFF),
]


@pytest.fixture(scope="module")
def mode_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "mode"), CASES)
def test_official_led_mode_sample_validates_and_round_trips(
        mode_dir, name, stored, mode):
    path = mode_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(path.read_bytes()) == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "mode"), CASES)
def test_led_mode_enumeration_decodes(mode_dir, name, stored, mode):
    profile = PfdProfile.from_path(mode_dir / name)
    assert profile.led_mode_raw == stored
    assert profile.led_mode == mode


@pytest.mark.parametrize(("name", "stored", "mode"), CASES)
def test_setting_led_mode_reproduces_official_field_change_only(
        mode_dir, name, stored, mode):
    source = PfdProfile.from_path(mode_dir / "NM16_ModeBreathing.pfd")
    source.set_led_mode(mode)
    assert source.to_bytes() == (mode_dir / name).read_bytes()


def test_setting_led_mode_does_not_alter_unrelated_data(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    excluded = (profile.group_section, "LedMode1")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    profile.set_led_mode(LedMode.WAVE)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert before == after


def test_unsupported_led_mode_is_rejected(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="unsupported LED mode"):
        profile.set_led_mode(6)


def test_unknown_stored_led_mode_is_preserved_losslessly(project_root):
    source = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    source.document.set(source.group_section, "LedMode1", "99")
    source.document.set(source.group_section, "FlashState1", "77")
    encoded = source.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.led_mode_raw == 99
    assert reparsed.led_mode is None
    assert reparsed.flash_state_raw == 77
    assert reparsed.to_bytes() == encoded
    assert "unknown-led-mode" in [issue.code for issue in validate_pfd(reparsed)]


def test_led_full_brightness_is_independent_from_other_controls(mode_dir):
    profile = PfdProfile.from_path(mode_dir / "NM17_ModeFull.pfd")
    assert profile.led_mode == LedMode.FULL_BRIGHTNESS
    assert profile.brightness == BrightnessLevel.MEDIUM
    assert profile.breathing_speed == BreathingSpeed.MEDIUM
    assert profile.led_mode_raw == 1
    assert profile.brightness_raw == 2
    assert profile.breathing_speed_raw == 5


def test_all_six_lighting_modes_are_distinct_and_use_led_mode_only(mode_dir):
    profiles = [
        PfdProfile.from_path(mode_dir / name)
        for name, _, _ in CASES
    ]
    assert [profile.led_mode for profile in profiles] == list(LedMode)
    assert [profile.led_mode_raw for profile in profiles] == list(range(6))
    assert {profile.flash_state_raw for profile in profiles} == {5}
    assert {profile.brightness_raw for profile in profiles} == {2}
    assert {profile.breathing_speed_raw for profile in profiles} == {5}


def test_common_dpi_tail_is_save_normalization_not_led_mode(
        project_root, mode_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [PfdProfile.from_path(mode_dir / case[0]) for case in CASES]
    assert all(sample.dpi_stage_struct == samples[0].dpi_stage_struct
               for sample in samples)
    assert samples[0].dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
    assert samples[0].dpi_stage_struct.checksum == 0xC5
    assert baseline.dpi_stage_struct.data[298:301] == b"\0\0\0"
    assert baseline.dpi_stage_struct.checksum == 0xF2
