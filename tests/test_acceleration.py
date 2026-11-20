from pathlib import Path

import pytest

from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM40_AccelerationMin.pfd", 1),
    ("NM41_AccelerationMid.pfd", 2),
    ("NM42_AccelerationMax.pfd", 4),
]


@pytest.fixture(scope="module")
def nonmacro_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "value"), CASES)
def test_official_acceleration_sample_validates_and_round_trips(
        nonmacro_dir, name, value):
    path = nonmacro_dir / name
    profile = PfdProfile.from_path(path)
    assert path.stat().st_size == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "value"), CASES)
def test_official_acceleration_value_decodes(nonmacro_dir, name, value):
    profile = PfdProfile.from_path(nonmacro_dir / name)
    assert profile.acceleration_raw == value
    assert profile.acceleration == value


def test_clean_baseline_acceleration_is_minimum(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    assert profile.acceleration_raw == 1
    assert profile.acceleration == 1


@pytest.mark.parametrize(("name", "value"), CASES)
def test_setter_reproduces_only_official_acceleration_field(
        nonmacro_dir, name, value):
    source = PfdProfile.from_path(nonmacro_dir / "NM40_AccelerationMin.pfd")
    source.set_acceleration(value)
    assert source.to_bytes() == (nonmacro_dir / name).read_bytes()


def test_all_confirmed_integer_levels_are_accepted(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    for value in range(1, 5):
        profile.set_acceleration(value)
        assert profile.acceleration_raw == value
        assert profile.acceleration == value


@pytest.mark.parametrize("value", [0, 5, -1, 1.5, True])
def test_invalid_acceleration_request_is_rejected(project_root, value):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    before = profile.to_bytes()
    with pytest.raises(ValueError, match="unsupported acceleration"):
        profile.set_acceleration(value)
    assert profile.to_bytes() == before


def test_unknown_raw_acceleration_is_preserved_and_warned(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.document.set(profile.group_section, "Acceleration", "99")
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.acceleration_raw == 99
    assert reparsed.acceleration is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-acceleration" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


def test_acceleration_raw_offsets_and_resave_tail(project_root, nonmacro_dir):
    baseline = (
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    ).read_bytes()
    expected_acceleration_byte = {
        "NM40_AccelerationMin.pfd": None,
        "NM41_AccelerationMid.pfd": (ord("1"), ord("2")),
        "NM42_AccelerationMax.pfd": (ord("1"), ord("4")),
    }
    tail_differences = {
        964: (ord("0"), ord("C")),
        965: (ord("0"), ord("4")),
        966: (ord("0"), ord("8")),
        967: (ord("0"), ord("7")),
        968: (ord("0"), ord("F")),
        1183: (ord("F"), ord("C")),
        1184: (ord("2"), ord("5")),
    }
    for name, _ in CASES:
        sample = (nonmacro_dir / name).read_bytes()
        differences = {
            index: (baseline[index], sample[index])
            for index in range(len(baseline))
            if baseline[index] != sample[index]
        }
        expected = dict(tail_differences)
        if expected_acceleration_byte[name] is not None:
            expected[133] = expected_acceleration_byte[name]
        assert differences == expected


def test_acceleration_setter_preserves_all_unrelated_data(nonmacro_dir):
    profile = PfdProfile.from_path(nonmacro_dir / "NM40_AccelerationMin.pfd")
    excluded = (profile.group_section, "Acceleration")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    dpi_before = profile.dpi_stage_struct
    profile.set_acceleration(4)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert after == before
    assert profile.dpi_stage_struct == dpi_before
    assert profile.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
