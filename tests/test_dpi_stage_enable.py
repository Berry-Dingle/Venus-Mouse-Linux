from pathlib import Path

import pytest

from venus_profile.checksum import ProfileStruct
from venus_profile.pfd import DpiStage, PfdProfile
from venus_profile.validation import validate_pfd


CASES = [
    ("NM35_DPI1_Disabled.pfd", DpiStage.STAGE_1, 368),
    ("NM36_DPI2_Disabled.pfd", DpiStage.STAGE_2, 400),
    ("NM37_DPI3_Disabled.pfd", DpiStage.STAGE_3, 432),
    ("NM38_DPI4_Disabled.pfd", DpiStage.STAGE_4, 464),
    ("NM39_DPI5_Disabled.pfd", DpiStage.STAGE_5, 496),
]


@pytest.fixture(scope="module")
def nonmacro_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "disabled_stage", "raw_offset"), CASES)
def test_official_disabled_sample_validates_and_round_trips(
        nonmacro_dir, name, disabled_stage, raw_offset):
    path = nonmacro_dir / name
    profile = PfdProfile.from_path(path)
    issues = validate_pfd(profile)
    assert not [issue for issue in issues if issue.severity == "error"]
    assert path.stat().st_size == 10654
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert profile.dpi_stage_struct.checksum == 0xC4
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "disabled_stage", "raw_offset"), CASES)
def test_disabled_stage_decodes_and_other_stages_remain_enabled(
        nonmacro_dir, name, disabled_stage, raw_offset):
    profile = PfdProfile.from_path(nonmacro_dir / name)
    assert profile.dpi_stage_enabled_raw(disabled_stage) == 0
    assert profile.dpi_stage_enabled(disabled_stage) is False
    for stage in DpiStage:
        if stage != disabled_stage:
            assert profile.dpi_stage_enabled_raw(stage) == 1
            assert profile.dpi_stage_enabled(stage) is True


@pytest.mark.parametrize(("name", "disabled_stage", "raw_offset"), CASES)
def test_raw_enable_setter_reproduces_official_file_only(
        nonmacro_dir, name, disabled_stage, raw_offset):
    source = PfdProfile.from_path(nonmacro_dir / "NM00_Resaved.pfd")
    selected = source.selected_dpi_stage
    source.set_dpi_stage_enabled(disabled_stage, False)
    assert source.to_bytes() == (nonmacro_dir / name).read_bytes()
    assert source.selected_dpi_stage == selected == DpiStage.STAGE_2


def test_disable_changes_only_enable_dword_and_preserves_dpi_tuple_data(
        nonmacro_dir):
    baseline = PfdProfile.from_path(nonmacro_dir / "NM00_Resaved.pfd")
    expected_dpi = [1000, 2000, 4000, 8200, 16400]
    for name, disabled_stage, _ in CASES:
        sample = PfdProfile.from_path(nonmacro_dir / name)
        assert [sample.dpi_stage_dpi(stage) for stage in DpiStage] == expected_dpi
        for stage in DpiStage:
            before = baseline.dpi_stage_value(stage)
            after = sample.dpi_stage_value(stage)
            assert (after.x_raw, after.y_raw, after.flag) == (
                before.x_raw, before.y_raw, before.flag,
            )
        before_data = baseline.dpi_stage_struct.data
        after_data = sample.dpi_stage_struct.data
        base = (disabled_stage.value - 1) * 16
        assert after_data[:base] == before_data[:base]
        assert after_data[base:base + 4] == b"\0\0\0\0"
        assert after_data[base + 4:] == before_data[base + 4:]


def test_saved_selected_stage_and_dpi_stage_num_are_constant(nonmacro_dir):
    profiles = [
        PfdProfile.from_path(nonmacro_dir / name) for name, _, _ in CASES
    ]
    assert all(p.selected_dpi_stage_raw == 1 for p in profiles)
    assert all(p.selected_dpi_stage == DpiStage.STAGE_2 for p in profiles)
    assert all(p.document.require(p.group_section, "DPIStageNum") == "5"
               for p in profiles)
    # The stage-2-disabled official file therefore stores a disabled selection.
    codes = [issue.code for issue in validate_pfd(profiles[1])]
    assert "selected-dpi-stage-disabled" in codes


@pytest.mark.parametrize(("name", "disabled_stage", "raw_offset"), CASES)
def test_exact_raw_file_difference_from_clean_baseline(
        project_root, nonmacro_dir, name, disabled_stage, raw_offset):
    baseline = (
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    ).read_bytes()
    sample = (nonmacro_dir / name).read_bytes()
    differences = {
        index: (baseline[index], sample[index])
        for index in range(len(baseline))
        if baseline[index] != sample[index]
    }
    expected = {
        raw_offset: (ord("1"), ord("0")),
        964: (ord("0"), ord("C")),
        965: (ord("0"), ord("4")),
        966: (ord("0"), ord("8")),
        967: (ord("0"), ord("7")),
        968: (ord("0"), ord("F")),
        1183: (ord("F"), ord("C")),
        1184: (ord("2"), ord("4")),
    }
    assert differences == expected


def test_unknown_enable_value_is_preserved_and_warned(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    blob = profile.dpi_stage_struct
    data = bytearray(blob.data)
    data[:4] = (7).to_bytes(4, "little")
    profile.document.set(
        profile.group_section,
        "DPIStageValue",
        ProfileStruct.from_data(data).encode(preserve_text=False),
    )
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.dpi_stage_enabled_raw(1) == 7
    assert reparsed.dpi_stage_enabled(1) is None
    assert reparsed.to_bytes() == encoded
    assert "unknown-dpi-enable" in [
        issue.code for issue in validate_pfd(reparsed)
    ]


def test_all_disabled_is_preserved_and_warned(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    for stage in DpiStage:
        profile.set_dpi_stage_enabled(stage, False)
    assert [profile.dpi_stage_enabled(stage) for stage in DpiStage] == [False] * 5
    assert "all-dpi-stages-disabled" in [
        issue.code for issue in validate_pfd(profile)
    ]


@pytest.mark.parametrize("stage", [0, 6])
def test_invalid_enable_stage_is_rejected(project_root, stage):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="invalid DPI stage"):
        profile.set_dpi_stage_enabled(stage, False)


@pytest.mark.parametrize("enabled", [0, 1, "yes", None])
def test_non_boolean_enable_request_is_rejected(project_root, enabled):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="must be boolean"):
        profile.set_dpi_stage_enabled(1, enabled)


def test_enable_setter_preserves_opaque_tail_and_unrelated_fields(nonmacro_dir):
    profile = PfdProfile.from_path(nonmacro_dir / "NM00_Resaved.pfd")
    excluded = (profile.group_section, "DPIStageValue")
    before_fields = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    before_tail = profile.dpi_stage_struct.data[80:]
    profile.set_dpi_stage_enabled(4, False)
    after_fields = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert after_fields == before_fields
    assert profile.dpi_stage_struct.data[80:] == before_tail
    assert profile.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
