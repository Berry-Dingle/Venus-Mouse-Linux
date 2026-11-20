from pathlib import Path
import struct

import pytest

from venus_profile.pfd import PaletteColor, PfdProfile, colorref_to_rgb
from venus_profile.validation import validate_pfd


CASES = [
    ("NM09_ColorRed.pfd", 65721, PaletteColor.RED, (185, 0, 1)),
    ("NM10_ColorGreen.pfd", 3454210, PaletteColor.GREEN, (2, 181, 52)),
    ("NM11_ColorBlue.pfd", 16715534, PaletteColor.BLUE, (14, 15, 255)),
]


@pytest.fixture(scope="module")
def colour_dir(project_root: Path) -> Path:
    return project_root / "ProfilesMacros" / "NonMacroTests"


@pytest.mark.parametrize(("name", "stored", "colour", "rgb"), CASES)
def test_official_colour_sample_validates_and_round_trips(
        colour_dir, name, stored, colour, rgb):
    path = colour_dir / name
    profile = PfdProfile.from_path(path)
    assert validate_pfd(profile) == []
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.to_bytes() == path.read_bytes()


@pytest.mark.parametrize(("name", "stored", "colour", "rgb"), CASES)
def test_primary_palette_colours_decode(
        colour_dir, name, stored, colour, rgb):
    profile = PfdProfile.from_path(colour_dir / name)
    assert profile.led_color_raw == stored
    assert profile.palette_color == colour
    assert profile.led_color_rgb == rgb
    assert colorref_to_rgb(stored) == rgb


@pytest.mark.parametrize(("name", "stored", "colour", "rgb"), CASES)
def test_setting_colour_reproduces_official_field_change_only(
        colour_dir, name, stored, colour, rgb):
    source = PfdProfile.from_path(colour_dir / "NM09_ColorRed.pfd")
    source.set_palette_color(colour)
    assert source.to_bytes() == (colour_dir / name).read_bytes()


def test_setting_colour_does_not_alter_unrelated_profile_data(project_root):
    path = project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd"
    profile = PfdProfile.from_path(path)
    excluded = (profile.group_section, "LedColor1")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    profile.set_palette_color(PaletteColor.BLUE)
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert before == after


def test_unknown_raw_colour_is_preserved_losslessly(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.document.set(profile.group_section, "LedColor1", str(0x123456))
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.led_color_raw == 0x123456
    assert reparsed.led_color_rgb == (0x56, 0x34, 0x12)
    assert reparsed.palette_color is None
    assert reparsed.to_bytes() == encoded


def test_unsupported_palette_colour_is_rejected(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="unsupported palette colour"):
        profile.set_palette_color(0x123456)


def test_complete_palette_matches_static_ldcfg_table(project_root):
    executable = project_root / "investigation" / "extracted" / "app" / "ldcfg.exe"
    table = struct.unpack_from("<33I", executable.read_bytes(), 0x1B4898)
    assert table == tuple(colour.value for colour in PaletteColor)


def test_common_dpi_resave_mutation_is_not_colour_dependent(
        project_root, colour_dir):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    samples = [PfdProfile.from_path(colour_dir / case[0]) for case in CASES]
    assert all(sample.dpi_stage_struct == samples[0].dpi_stage_struct
               for sample in samples)
    differences = [
        (index, baseline.dpi_stage_struct.data[index],
         samples[0].dpi_stage_struct.data[index])
        for index in range(408)
        if baseline.dpi_stage_struct.data[index] != samples[0].dpi_stage_struct.data[index]
    ]
    assert differences == [(298, 0, 0x0C), (299, 0, 0x48), (300, 0, 0xC9)]
    assert baseline.dpi_stage_struct.checksum == 0xF2
    assert samples[0].dpi_stage_struct.checksum == 0x0F
