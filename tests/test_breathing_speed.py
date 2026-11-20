from pathlib import Path

import pytest

from venus_profile.pfd import BreathingSpeed, PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM12_BreathLow.pfd", 8, BreathingSpeed.LOW),
    ("NM13_BreathMed.pfd", 5, BreathingSpeed.MEDIUM),
    ("NM14_BreathHigh.pfd", 2, BreathingSpeed.HIGH),
    ("NM15_BreathFull.pfd", 0, BreathingSpeed.FULL_BRIGHTNESS),
]


@pytest.fixture(scope="module")
def breathing_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "speed"), CASES)
def test_official_breathing_sample_validates_and_round_trips(
        breathing_dir, name, stored, speed):
    path = breathing_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(path.read_bytes()) == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "speed"), CASES)
def test_breathing_speed_enumeration_decodes(
        breathing_dir, name, stored, speed):
    profile = PfdProfile.from_path(breathing_dir / name)
    assert profile.breathing_speed_raw == stored
    assert profile.breathing_speed == speed


@pytest.mark.parametrize(("name", "stored", "speed"), CASES)
def test_setting_breathing_speed_reproduces_official_field_change_only(
        breathing_dir, name, stored, speed):
    # All four editor-resaved files have identical non-speed data.
    source = PfdProfile.from_path(breathing_dir / "NM13_BreathMed.pfd")
    source.set_breathing_speed(speed)
    assert source.to_bytes() == (breathing_dir / name).read_bytes()


def test_setting_breathing_speed_does_not_alter_unrelated_data(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    excluded = (profile.group_section, "BreathState1")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    profile.set_breathing_speed(BreathingSpeed.LOW)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert before == after


def test_unsupported_breathing_speed_is_rejected(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="unsupported breathing speed"):
        profile.set_breathing_speed(3)


def test_unknown_stored_breathing_speed_is_preserved_losslessly(project_root):
    source = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    source.document.set(source.group_section, "BreathState1", "99")
    encoded = source.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.breathing_speed_raw == 99
    assert reparsed.breathing_speed is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-breathing-speed" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


def test_common_dpi_resave_mutation_is_not_breathing_speed_dependent(
        project_root, breathing_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [PfdProfile.from_path(breathing_dir / case[0]) for case in CASES]
    assert all(sample.dpi_stage_struct == samples[0].dpi_stage_struct
               for sample in samples)
    differences = [
        (index, baseline.dpi_stage_struct.data[index],
         samples[0].dpi_stage_struct.data[index])
        for index in range(408)
        if baseline.dpi_stage_struct.data[index] != samples[0].dpi_stage_struct.data[index]
    ]
    assert differences == [(298, 0, 0x0C), (299, 0, 0x48), (300, 0, 0x7F)]
    assert baseline.dpi_stage_struct.checksum == 0xF2
    assert samples[0].dpi_stage_struct.checksum == 0xC5
