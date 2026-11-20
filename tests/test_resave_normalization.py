from pathlib import Path

import pytest

from venus_profile.pfd import (
    BreathingSpeed,
    BrightnessLevel,
    PaletteColor,
    PfdProfile,
    PollingRateHz,
)
from venus_profile.validation import validate_pfd


@pytest.fixture
def baseline(project_root: Path) -> tuple[Path, PfdProfile]:
    path = project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    return path, PfdProfile.from_path(path)


@pytest.fixture
def resaved(project_root: Path) -> tuple[Path, PfdProfile]:
    path = project_root / "ProfilesMacros" / "NonMacroTests" / "NM00_Resaved.pfd"
    return path, PfdProfile.from_path(path)


def test_noop_resave_validates_and_round_trips(resaved):
    path, profile = resaved
    assert len(path.read_bytes()) == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == path.read_bytes()


def test_noop_resave_semantic_fields_match_baseline(baseline, resaved):
    _, original = baseline
    _, saved = resaved
    excluded = (original.group_section, "DPIStageValue")
    original_fields = {
        (section, key): value
        for section in original.document.sections
        for key, value in original.document.items(section)
        if (section, key) != excluded
    }
    saved_fields = {
        (section, key): value
        for section in saved.document.sections
        for key, value in saved.document.items(section)
        if (section, key) != excluded
    }
    assert saved_fields == original_fields


def test_noop_resave_has_only_confirmed_dpi_mutation(baseline, resaved):
    original_path, original = baseline
    saved_path, saved = resaved
    assert [
        (index, original.dpi_stage_struct.data[index],
         saved.dpi_stage_struct.data[index])
        for index in range(408)
        if original.dpi_stage_struct.data[index] != saved.dpi_stage_struct.data[index]
    ] == [(298, 0, 0x0C), (299, 0, 0x48), (300, 0, 0x7F)]
    assert original.dpi_stage_struct.checksum == 0xF2
    assert saved.dpi_stage_struct.checksum == 0xC5
    assert saved.dpi_stage_struct.checksum == sum(saved.dpi_stage_struct.data) & 0xFF

    original_bytes = original_path.read_bytes()
    saved_bytes = saved_path.read_bytes()
    assert [
        index for index, pair in enumerate(zip(original_bytes, saved_bytes))
        if pair[0] != pair[1]
    ] == [964, 965, 966, 967, 968, 1183, 1184]
    assert original_bytes[964:969] == b"00000"
    assert saved_bytes[964:969] == b"C487F"
    assert original_bytes[1183:1185] == b"F2"
    assert saved_bytes[1183:1185] == b"C5"


def test_prior_batches_have_same_shape_but_session_variant_byte(project_root):
    test_dir = project_root / "ProfilesMacros" / "NonMacroTests"
    expected = {
        "NM01_Poll125.pfd": (b"\x0c\x48\xbd", 0x03),
        "NM05_BrightOff.pfd": (b"\x0c\x48\x18", 0x5E),
        "NM09_ColorRed.pfd": (b"\x0c\x48\xc9", 0x0F),
        "NM12_BreathLow.pfd": (b"\x0c\x48\x7f", 0xC5),
        "NM00_Resaved.pfd": (b"\x0c\x48\x7f", 0xC5),
    }
    for name, (data, checksum) in expected.items():
        profile = PfdProfile.from_path(test_dir / name)
        assert profile.dpi_stage_struct.data[298:301] == data
        assert profile.dpi_stage_struct.checksum == checksum


@pytest.mark.parametrize("feature", ["polling", "brightness", "colour", "breathing"])
def test_feature_setters_do_not_reproduce_resave_mutation(baseline, feature):
    _, profile = baseline
    before = profile.dpi_stage_struct
    if feature == "polling":
        profile.set_polling_rate_hz(PollingRateHz.HZ_125)
    elif feature == "brightness":
        profile.set_brightness(BrightnessLevel.HIGH)
    elif feature == "colour":
        profile.set_palette_color(PaletteColor.BLUE)
    else:
        profile.set_breathing_speed(BreathingSpeed.LOW)
    assert profile.dpi_stage_struct == before


def test_feature_models_are_identical_across_noop_resave(baseline, resaved):
    _, original = baseline
    _, saved = resaved
    assert saved.polling_rate_hz == original.polling_rate_hz
    assert saved.brightness == original.brightness
    assert saved.breathing_speed == original.breathing_speed
    assert saved.led_color_raw == original.led_color_raw
    assert saved.buttons == original.buttons
    assert saved.macro_assignments == original.macro_assignments == []
