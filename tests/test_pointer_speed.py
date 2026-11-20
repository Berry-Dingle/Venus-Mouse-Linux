from pathlib import Path

import pytest

from venus_profile.pfd import PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM43_PointerSpeedMin.pfd", 1),
    ("NM44_PointerSpeedMid.pfd", 10),
    ("NM45_PointerSpeedMax.pfd", 20),
]


@pytest.fixture(scope="module")
def nonmacro_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "value"), CASES)
def test_official_pointer_speed_sample_validates_and_round_trips(
        nonmacro_dir, name, value):
    path = nonmacro_dir / name
    profile = PfdProfile.from_path(path)
    assert path.stat().st_size == (10653 if value == 1 else 10654)
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "value"), CASES)
def test_official_pointer_speed_decodes(nonmacro_dir, name, value):
    profile = PfdProfile.from_path(nonmacro_dir / name)
    assert profile.pointer_speed_raw == value
    assert profile.pointer_speed == value


def test_clean_baseline_pointer_speed_is_ten(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    assert profile.pointer_speed_raw == 10
    assert profile.pointer_speed == 10


@pytest.mark.parametrize(("name", "value"), CASES)
def test_setter_reproduces_only_official_pointer_speed_field(
        nonmacro_dir, name, value):
    source = PfdProfile.from_path(nonmacro_dir / "NM43_PointerSpeedMin.pfd")
    source.set_pointer_speed(value)
    assert source.to_bytes() == (nonmacro_dir / name).read_bytes()


def test_every_confirmed_pointer_speed_level_is_accepted(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    for value in range(1, 21):
        profile.set_pointer_speed(value)
        assert profile.pointer_speed_raw == value
        assert profile.pointer_speed == value


@pytest.mark.parametrize("value", [0, 21, -1, 1.5, True])
def test_invalid_pointer_speed_request_is_rejected(project_root, value):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    before = profile.to_bytes()
    with pytest.raises(ValueError, match="unsupported pointer speed"):
        profile.set_pointer_speed(value)
    assert profile.to_bytes() == before


def test_unknown_raw_pointer_speed_is_preserved_and_warned(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.document.set(profile.group_section, "MouseSensitivity", "99")
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.pointer_speed_raw == 99
    assert reparsed.pointer_speed is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-pointer-speed" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


def test_pointer_speed_raw_location_and_representation(project_root, nonmacro_dir):
    paths = {
        "baseline": project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd",
        **{name: nonmacro_dir / name for name, _ in CASES},
    }
    expected = {
        "baseline": b"10",
        "NM43_PointerSpeedMin.pfd": b"1",
        "NM44_PointerSpeedMid.pfd": b"10",
        "NM45_PointerSpeedMax.pfd": b"20",
    }
    token = b"MouseSensitivity="
    for name, path in paths.items():
        content = path.read_bytes()
        start = content.index(token) + len(token)
        end = content.index(b"\r\n", start)
        assert start == 70
        assert content[start:end] == expected[name]


def test_pointer_speed_does_not_change_xy_or_other_fields(nonmacro_dir):
    profiles = [
        PfdProfile.from_path(nonmacro_dir / name) for name, _ in CASES
    ]
    group = profiles[0].group_section
    isolated = {"MouseSensitivity", "DPIStageValue"}
    reference = {
        key: value for key, value in profiles[0].document.items(group)
        if key not in isolated
    }
    assert all({
        key: value for key, value in profile.document.items(group)
        if key not in isolated
    } == reference for profile in profiles)
    assert all(profile.document.require(group, "MouseSpeedX") == "10"
               for profile in profiles)
    assert all(profile.document.require(group, "MouseSpeedY") == "10"
               for profile in profiles)
    assert all(profile.document.require(group, "XYSensitivityEnabled") == "0"
               for profile in profiles)


def test_pointer_speed_setter_preserves_unrelated_data_and_dpi_tail(nonmacro_dir):
    profile = PfdProfile.from_path(nonmacro_dir / "NM44_PointerSpeedMid.pfd")
    excluded = (profile.group_section, "MouseSensitivity")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    dpi_before = profile.dpi_stage_struct
    profile.set_pointer_speed(20)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert after == before
    assert profile.dpi_stage_struct == dpi_before
    assert profile.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
