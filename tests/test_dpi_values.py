from pathlib import Path

import pytest

from venus_profile.pfd import (
    DpiStage,
    PfdProfile,
    dpi_raw_to_dpi,
    dpi_to_raw,
)
from venus_profile.validation import validate_pfd


CASES = [
    ("NM27_DPI1_200.pfd", 200, 1, 0xB5),
    ("NM28_DPI1_400.pfd", 400, 3, 0xB9),
    ("NM29_DPI1_800.pfd", 800, 7, 0xC1),
    ("NM30_DPI1_1600.pfd", 1600, 15, 0xD1),
    ("NM31_DPI1_3200.pfd", 3200, 31, 0xF1),
    ("NM32_DPI1_6400.pfd", 6400, 62, 0x2F),
    ("NM33_DPI1_12000.pfd", 12000, 90, 0x67),
    ("NM34_DPI1_16400.pfd", 16400, 106, 0x87),
]


@pytest.fixture(scope="module")
def dpi_value_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "dpi", "raw", "checksum"), CASES)
def test_official_dpi_value_sample_validates_and_round_trips(
        dpi_value_dir, name, dpi, raw, checksum):
    path = dpi_value_dir / name
    profile = PfdProfile.from_path(path)
    assert path.stat().st_size == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert profile.dpi_stage_struct.checksum == checksum
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "dpi", "raw", "checksum"), CASES)
def test_controlled_dpi_value_decodes(dpi_value_dir, name, dpi, raw, checksum):
    profile = PfdProfile.from_path(dpi_value_dir / name)
    assert profile.dpi_stage_value_raw(1) == (raw, raw)
    assert profile.dpi_stage_dpi(DpiStage.STAGE_1) == dpi
    assert profile.dpi_stage_value(1).enabled == 1
    assert profile.dpi_stage_value(1).flag == 1
    assert dpi_raw_to_dpi(raw) == dpi
    assert dpi_to_raw(dpi) == raw


@pytest.mark.parametrize(("name", "dpi", "raw", "checksum"), CASES)
def test_setting_dpi_reproduces_only_official_dpi_bytes(
        dpi_value_dir, name, dpi, raw, checksum):
    source = PfdProfile.from_path(dpi_value_dir / "NM27_DPI1_200.pfd")
    before_data = source.dpi_stage_struct.data
    before_document = {
        (section, key): value
        for section in source.document.sections
        for key, value in source.document.items(section)
        if key != "DPIStageValue"
    }
    source.set_dpi_stage_dpi(1, dpi)
    target = PfdProfile.from_path(dpi_value_dir / name)
    assert source.to_bytes() == target.to_bytes()
    assert source.dpi_stage_struct.data[:4] == before_data[:4]
    assert source.dpi_stage_struct.data[12:] == before_data[12:]
    assert before_document == {
        (section, key): value
        for section in source.document.sections
        for key, value in source.document.items(section)
        if key != "DPIStageValue"
    }


def test_baseline_all_five_dpi_values_decode(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    assert [profile.dpi_stage_dpi(stage) for stage in range(1, 6)] == [
        1000, 2000, 4000, 8200, 16400,
    ]
    assert [profile.dpi_stage_value_raw(stage) for stage in range(1, 6)] == [
        (9, 9), (19, 19), (39, 39), (71, 71), (106, 106),
    ]


def test_dpi_conversion_covers_exact_editor_domain():
    legal = (
        list(range(200, 6201, 100))
        + list(range(6400, 12401, 200))
        + list(range(12600, 16201, 300))
        + [16400]
    )
    assert len(legal) == 106
    assert [dpi_raw_to_dpi(raw) for raw in range(1, 107)] == legal
    assert [dpi_to_raw(dpi) for dpi in legal] == list(range(1, 107))


def test_setting_one_stage_preserves_other_stages_selection_flags_and_tail(
        dpi_value_dir):
    profile = PfdProfile.from_path(dpi_value_dir / "NM27_DPI1_200.pfd")
    before = profile.dpi_stage_struct.data
    other_values = [profile.dpi_stage_value(stage) for stage in range(2, 6)]
    selected = profile.selected_dpi_stage
    profile.set_dpi_stage_dpi(DpiStage.STAGE_1, 16400)
    after = profile.dpi_stage_struct.data
    assert after[:4] == before[:4]
    assert after[12:] == before[12:]
    assert [profile.dpi_stage_value(stage) for stage in range(2, 6)] == other_values
    assert profile.selected_dpi_stage == selected == DpiStage.STAGE_2
    assert [profile.dpi_stage_value(stage).enabled for stage in range(1, 6)] == [1] * 5
    assert after[298:301] == before[298:301] == b"\x0c\x48\x7f"


@pytest.mark.parametrize("dpi", [100, 250, 6300, 12500, 12800, 16500])
def test_illegal_dpi_is_rejected(project_root, dpi):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    before = profile.to_bytes()
    with pytest.raises(ValueError, match="unsupported DPI"):
        profile.set_dpi_stage_dpi(1, dpi)
    assert profile.to_bytes() == before


@pytest.mark.parametrize("stage", [0, 6])
def test_invalid_dpi_stage_is_rejected(project_root, stage):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="invalid DPI stage"):
        profile.set_dpi_stage_dpi(stage, 1000)


def test_unknown_and_asymmetric_dpi_values_remain_lossless(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    blob = profile.dpi_stage_struct
    data = bytearray(blob.data)
    data[4:8] = (107).to_bytes(4, "little")
    data[8:12] = (9).to_bytes(4, "little")
    from venus_profile.checksum import ProfileStruct
    profile.document.set(
        profile.group_section,
        "DPIStageValue",
        ProfileStruct.from_data(data).encode(preserve_text=False),
    )
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.dpi_stage_value_raw(1) == (107, 9)
    assert reparsed.dpi_stage_dpi(1) is None
    assert reparsed.to_bytes() == encoded
    codes = [issue.code for issue in validate_pfd(reparsed)]
    assert "unknown-dpi-raw" in codes
    assert "asymmetric-dpi-stage" in codes
