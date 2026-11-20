from pathlib import Path
import struct

import pytest

from venus_profile.pfd import DpiStage, PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM22_DPISelected1.pfd", 0, DpiStage.STAGE_1),
    ("NM23_DPISelected2.pfd", 1, DpiStage.STAGE_2),
    ("NM24_DPISelected3.pfd", 2, DpiStage.STAGE_3),
    ("NM25_DPISelected4.pfd", 3, DpiStage.STAGE_4),
    ("NM26_DPISelected5.pfd", 4, DpiStage.STAGE_5),
]


@pytest.fixture(scope="module")
def dpi_selected_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "stage"), CASES)
def test_official_selected_dpi_sample_validates_and_round_trips(
        dpi_selected_dir, name, stored, stage):
    path = dpi_selected_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(path.read_bytes()) == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "stage"), CASES)
def test_selected_dpi_stage_decodes(dpi_selected_dir, name, stored, stage):
    profile = PfdProfile.from_path(dpi_selected_dir / name)
    assert profile.selected_dpi_stage_raw == stored
    assert profile.selected_dpi_stage == stage
    assert profile.selected_dpi_stage.value == stored + 1


@pytest.mark.parametrize(("name", "stored", "stage"), CASES)
def test_setting_selected_stage_reproduces_official_field_change_only(
        dpi_selected_dir, name, stored, stage):
    source = PfdProfile.from_path(dpi_selected_dir / "NM23_DPISelected2.pfd")
    source.set_selected_dpi_stage(stage)
    assert source.to_bytes() == (dpi_selected_dir / name).read_bytes()


def test_all_stage_values_and_enable_flags_remain_unchanged(dpi_selected_dir):
    profiles = [
        PfdProfile.from_path(dpi_selected_dir / name)
        for name, _, _ in CASES
    ]
    assert all(
        profile.dpi_stage_struct == profiles[0].dpi_stage_struct
        for profile in profiles
    )
    # Five 16-byte tuples: enabled, X sensor step, Y sensor step, flag.
    tuples = [
        struct.unpack_from("<4I", profiles[0].dpi_stage_struct.data, offset)
        for offset in range(0, 80, 16)
    ]
    assert tuples == [
        (1, 9, 9, 1),
        (1, 19, 19, 1),
        (1, 39, 39, 1),
        (1, 71, 71, 1),
        (1, 106, 106, 1),
    ]
    assert all(profile.document.get(profile.group_section, "DPIStageNum") == "5"
               for profile in profiles)
    assert all(profile.document.get(profile.group_section, "DPICurrentX") == "14"
               for profile in profiles)
    assert all(profile.document.get(profile.group_section, "DPICurrentY") == "14"
               for profile in profiles)


def test_setting_selected_stage_does_not_alter_unrelated_data(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    excluded = (profile.group_section, "DPI")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    profile.set_selected_dpi_stage(DpiStage.STAGE_5)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert before == after


def test_unknown_selected_stage_is_preserved_losslessly(project_root):
    source = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    source.document.set(source.group_section, "DPI", "99")
    encoded = source.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.selected_dpi_stage_raw == 99
    assert reparsed.selected_dpi_stage is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-selected-dpi-stage" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


@pytest.mark.parametrize("stage", [0, 6])
def test_invalid_selected_stage_is_rejected(project_root, stage):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="invalid DPI stage"):
        profile.set_selected_dpi_stage(stage)


def test_dpi_tail_is_isolated_from_selected_stage(
        project_root, dpi_selected_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [
        PfdProfile.from_path(dpi_selected_dir / name)
        for name, _, _ in CASES
    ]
    assert all(sample.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
               for sample in samples)
    assert all(sample.dpi_stage_struct.checksum == 0xC5 for sample in samples)
    assert baseline.dpi_stage_struct.data[298:301] == b"\0\0\0"
    before = baseline.dpi_stage_struct
    baseline.set_selected_dpi_stage(DpiStage.STAGE_5)
    assert baseline.dpi_stage_struct == before
