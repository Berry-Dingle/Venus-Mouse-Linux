from pathlib import Path

import pytest

from venus_profile.pfd import BrightnessLevel, PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM05_BrightOff.pfd", 0, BrightnessLevel.OFF),
    ("NM06_BrightLow.pfd", 1, BrightnessLevel.LOW),
    ("NM07_BrightMed.pfd", 2, BrightnessLevel.MEDIUM),
    ("NM08_BrightHigh.pfd", 3, BrightnessLevel.HIGH),
]


@pytest.fixture(scope="module")
def brightness_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "level"), CASES)
def test_official_brightness_sample_validates_and_round_trips(
        brightness_dir, name, stored, level):
    path = brightness_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(path.read_bytes()) == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "level"), CASES)
def test_brightness_enumeration_decodes(brightness_dir, name, stored, level):
    profile = PfdProfile.from_path(brightness_dir / name)
    assert profile.brightness_raw == stored
    assert profile.brightness == level


@pytest.mark.parametrize(("name", "stored", "level"), CASES)
def test_setting_brightness_reproduces_official_field_change_only(
        brightness_dir, name, stored, level):
    # All four editor-resaved brightness files have identical non-brightness data.
    source = PfdProfile.from_path(brightness_dir / "NM07_BrightMed.pfd")
    source.set_brightness(level)
    assert source.to_bytes() == (brightness_dir / name).read_bytes()


def test_setting_brightness_does_not_alter_unrelated_profile_data(project_root):
    path = project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    profile = PfdProfile.from_path(path)
    excluded = (profile.group_section, "LedState1")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    profile.set_brightness(BrightnessLevel.HIGH)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert before == after


def test_unsupported_brightness_is_rejected(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="unsupported brightness"):
        profile.set_brightness(4)


def test_unknown_stored_brightness_is_preserved_losslessly(project_root):
    source = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    source.document.set(source.group_section, "LedState1", "99")
    encoded = source.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.brightness_raw == 99
    assert reparsed.brightness is None
    assert reparsed.to_bytes() == encoded
    issues = validate_pfd(reparsed)
    assert "unknown-brightness" in [issue.code for issue in issues]


def test_common_dpi_resave_mutation_is_not_brightness_dependent(
        project_root, brightness_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [PfdProfile.from_path(brightness_dir / case[0]) for case in CASES]
    assert all(sample.dpi_stage_struct == samples[0].dpi_stage_struct
               for sample in samples)
    differences = [
        (index, baseline.dpi_stage_struct.data[index],
         samples[0].dpi_stage_struct.data[index])
        for index in range(408)
        if baseline.dpi_stage_struct.data[index] != samples[0].dpi_stage_struct.data[index]
    ]
    assert differences == [(298, 0, 0x0C), (299, 0, 0x48), (300, 0, 0x18)]
    assert baseline.dpi_stage_struct.checksum == 0xF2
    assert samples[0].dpi_stage_struct.checksum == 0x5E
