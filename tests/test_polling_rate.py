from pathlib import Path

import pytest

from venus_profile.pfd import PfdProfile, PollingRateHz
from venus_profile.validation import validate_pfd


CASES = [
    ("NM01_Poll125.pfd", 8, PollingRateHz.HZ_125),
    ("NM02_Poll250.pfd", 4, PollingRateHz.HZ_250),
    ("NM03_Poll500.pfd", 2, PollingRateHz.HZ_500),
    ("NM04_Poll1000.pfd", 1, PollingRateHz.HZ_1000),
]


@pytest.fixture(scope="module")
def polling_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "rate"), CASES)
def test_official_polling_sample_validates_and_round_trips(
        polling_dir, name, stored, rate):
    path = polling_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "rate"), CASES)
def test_polling_enumeration_decodes(polling_dir, name, stored, rate):
    profile = PfdProfile.from_path(polling_dir / name)
    assert profile.polling_rate_raw == stored
    assert profile.polling_rate_hz == rate
    assert profile.polling_rate_hz.value == rate.value


@pytest.mark.parametrize(("name", "stored", "rate"), CASES)
def test_setting_rate_reproduces_official_field_change(
        polling_dir, name, stored, rate):
    # All four editor-resaved files share identical non-polling content.
    source = PfdProfile.from_path(polling_dir / "NM03_Poll500.pfd")
    source.set_polling_rate_hz(rate)
    assert source.to_bytes() == (polling_dir / name).read_bytes()


def test_setting_polling_rate_does_not_alter_unrelated_profile_data(
        project_root):
    path = project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    profile = PfdProfile.from_path(path)
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if not (section == profile.group_section and key == "PollingRate")
    }
    dpi_before = profile.dpi_stage_struct
    buttons_before = [button.blob for button in profile.buttons]
    profile.set_polling_rate_hz(PollingRateHz.HZ_125)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if not (section == profile.group_section and key == "PollingRate")
    }
    assert before == after
    assert profile.dpi_stage_struct == dpi_before
    assert [button.blob for button in profile.buttons] == buttons_before


def test_unsupported_polling_rate_is_rejected(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="unsupported polling rate"):
        profile.set_polling_rate_hz(333)


def test_unknown_stored_polling_enum_is_preserved_losslessly(project_root):
    source = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    source.document.set(source.group_section, "PollingRate", "99")
    encoded = source.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.polling_rate_raw == 99
    assert reparsed.polling_rate_hz is None
    assert reparsed.to_bytes() == encoded
    issues = validate_pfd(reparsed)
    assert "unknown-polling-rate" in [issue.code for issue in issues]


def test_common_dpi_resave_mutation_is_not_rate_dependent(
        project_root, polling_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [PfdProfile.from_path(polling_dir / case[0]) for case in CASES]
    assert all(sample.dpi_stage_struct == samples[0].dpi_stage_struct
               for sample in samples)
    differences = [
        (index, baseline.dpi_stage_struct.data[index],
         samples[0].dpi_stage_struct.data[index])
        for index in range(408)
        if baseline.dpi_stage_struct.data[index] != samples[0].dpi_stage_struct.data[index]
    ]
    assert differences == [(298, 0, 0x0C), (299, 0, 0x48), (300, 0, 0xBD)]
    assert baseline.dpi_stage_struct.checksum == 0xF2
    assert samples[0].dpi_stage_struct.checksum == 0x03
