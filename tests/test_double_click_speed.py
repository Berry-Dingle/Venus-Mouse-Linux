from pathlib import Path

import pytest

from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM49_DoubleClickMin.pfd", 1, 830),
    ("NM50_DoubleClickMid.pfd", 5, 550),
    ("NM51_DoubleClickMax.pfd", 10, 200),
]


@pytest.fixture(scope="module")
def nonmacro_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "level", "milliseconds"), CASES)
def test_official_double_click_sample_validates_and_round_trips(
        nonmacro_dir, name, level, milliseconds):
    path = nonmacro_dir / name
    profile = PfdProfile.from_path(path)
    assert path.stat().st_size == (10655 if level == 10 else 10654)
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "level", "milliseconds"), CASES)
def test_official_double_click_speed_and_time_decode(
        nonmacro_dir, name, level, milliseconds):
    profile = PfdProfile.from_path(nonmacro_dir / name)
    assert profile.double_click_speed_raw == level
    assert profile.double_click_speed == level
    assert profile.double_click_time_ms == milliseconds


def test_clean_baseline_double_click_speed(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    assert profile.double_click_speed_raw == 5
    assert profile.double_click_speed == 5
    assert profile.double_click_time_ms == 550


@pytest.mark.parametrize(("name", "level", "milliseconds"), CASES)
def test_setter_reproduces_only_official_double_click_field(
        nonmacro_dir, name, level, milliseconds):
    source = PfdProfile.from_path(nonmacro_dir / "NM49_DoubleClickMin.pfd")
    source.set_double_click_speed(level)
    assert source.to_bytes() == (nonmacro_dir / name).read_bytes()


def test_every_double_click_level_and_static_time_formula(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    for level in range(1, 11):
        profile.set_double_click_speed(level)
        assert profile.double_click_speed == level
        assert profile.double_click_time_ms == 900 - level * 70


@pytest.mark.parametrize("value", [0, 11, -1, 1.5, True])
def test_invalid_double_click_request_is_rejected(project_root, value):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    before = profile.to_bytes()
    with pytest.raises(ValueError, match="unsupported double-click speed"):
        profile.set_double_click_speed(value)
    assert profile.to_bytes() == before


def test_unknown_raw_double_click_speed_is_preserved_and_warned(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.document.set(profile.group_section, "DoubleClickSpeed", "99")
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.double_click_speed_raw == 99
    assert reparsed.double_click_speed is None
    assert reparsed.double_click_time_ms is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-double-click-speed" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


def test_double_click_raw_location_and_representation(project_root, nonmacro_dir):
    paths = {
        "baseline": project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd",
        **{name: nonmacro_dir / name for name, _, _ in CASES},
    }
    expected = {
        "baseline": b"5",
        "NM49_DoubleClickMin.pfd": b"1",
        "NM50_DoubleClickMid.pfd": b"5",
        "NM51_DoubleClickMax.pfd": b"10",
    }
    token = b"DoubleClickSpeed="
    for name, path in paths.items():
        content = path.read_bytes()
        start = content.index(token) + len(token)
        end = content.index(b"\r\n", start)
        assert start == 91
        assert content[start:end] == expected[name]


def test_double_click_does_not_change_other_general_fields(nonmacro_dir):
    profiles = [
        PfdProfile.from_path(nonmacro_dir / name) for name, _, _ in CASES
    ]
    group = profiles[0].group_section
    isolated = {"DoubleClickSpeed", "DPIStageValue"}
    reference = {
        key: value for key, value in profiles[0].document.items(group)
        if key not in isolated
    }
    assert all({
        key: value for key, value in profile.document.items(group)
        if key not in isolated
    } == reference for profile in profiles)


def test_double_click_setter_preserves_unrelated_data_and_dpi_tail(nonmacro_dir):
    profile = PfdProfile.from_path(nonmacro_dir / "NM50_DoubleClickMid.pfd")
    excluded = (profile.group_section, "DoubleClickSpeed")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    dpi_before = profile.dpi_stage_struct
    profile.set_double_click_speed(10)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert after == before
    assert profile.dpi_stage_struct == dpi_before
    assert profile.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
