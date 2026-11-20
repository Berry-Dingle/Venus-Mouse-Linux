from pathlib import Path

import pytest

from venus_profile.pfd import (
    PaletteColor,
    PfdProfile,
    parse_rgb_hex,
    rgb_to_colorref,
)
from venus_profile.validation import validate_pfd


@pytest.fixture(scope="module")
def custom_path(project_root: Path) -> Path:
    return (
        project_root / "ProfilesMacros" / "NonMacroTests"
        / "NM52_CustomColor_123456.pfd"
    )


def test_official_custom_color_validates_and_round_trips(custom_path):
    profile = PfdProfile.from_path(custom_path)
    assert custom_path.stat().st_size == 10658
    assert len(profile.document.sections) == 21
    assert len(profile.document.items(profile.group_section)) == 43
    assert len(profile.buttons) == 20
    assert profile.macro_assignments == []
    assert profile.dpi_stage_struct.valid
    assert validate_pfd(profile) == []
    assert profile.to_bytes() == custom_path.read_bytes()


def test_official_picker_stores_typed_digits_without_colorref_swap(custom_path):
    profile = PfdProfile.from_path(custom_path)
    assert profile.led_color_raw == 1_193_046
    assert profile.led_color_raw == 0x00123456
    assert profile.led_color_raw.to_bytes(4, "little") == b"\x56\x34\x12\x00"
    assert profile.led_color_rgb == (86, 52, 18)
    assert profile.led_color_hex == "#563412"
    assert profile.led_color_raw_hex == "#123456"
    assert profile.palette_color is None


def test_standard_rgb_123456_encodes_as_colorref():
    assert rgb_to_colorref(18, 52, 86) == 0x00563412
    assert rgb_to_colorref(18, 52, 86).to_bytes(4, "little") == (
        b"\x12\x34\x56\x00"
    )


@pytest.mark.parametrize("value", ["#123456", "123456", "#123456".lower()])
def test_hex_setter_encodes_standard_rgb(project_root, value):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.set_led_color_hex(value)
    assert profile.led_color_raw == 0x00563412
    assert profile.led_color_rgb == (18, 52, 86)
    assert profile.led_color_hex == "#123456"


@pytest.mark.parametrize(
    ("rgb", "colorref", "encoded"),
    [
        ((0, 0, 0), 0x00000000, b"\x00\x00\x00\x00"),
        ((255, 255, 255), 0x00FFFFFF, b"\xff\xff\xff\x00"),
        ((255, 0, 0), 0x000000FF, b"\xff\x00\x00\x00"),
        ((0, 255, 0), 0x0000FF00, b"\x00\xff\x00\x00"),
        ((0, 0, 255), 0x00FF0000, b"\x00\x00\xff\x00"),
    ],
)
def test_rgb_boundaries_and_channel_order(project_root, rgb, colorref, encoded):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.set_led_color_rgb(*rgb)
    assert profile.led_color_raw == colorref
    assert profile.led_color_raw.to_bytes(4, "little") == encoded
    assert profile.led_color_rgb == rgb


@pytest.mark.parametrize(
    "rgb",
    [(-1, 0, 0), (256, 0, 0), (0, 1.5, 0), (0, True, 0), ("1", 0, 0)],
)
def test_invalid_rgb_channels_are_rejected(project_root, rgb):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="RGB channels"):
        profile.set_led_color_rgb(*rgb)


@pytest.mark.parametrize(
    "value",
    ["", "#12345", "#1234567", "0x123456", "##123456", "#12GG56", 123456],
)
def test_malformed_hex_is_rejected(project_root, value):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    with pytest.raises(ValueError, match="RGB hex"):
        profile.set_led_color_hex(value)


def test_palette_api_remains_compatible(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "NonMacroTests"
        / "NM09_ColorRed.pfd")
    assert profile.palette_color == PaletteColor.RED
    rgb = PaletteColor.RED.rgb
    profile.set_led_color_rgb(*rgb)
    assert profile.led_color_raw == PaletteColor.RED.value
    assert profile.palette_color == PaletteColor.RED


def test_arbitrary_non_palette_color_is_lossless(project_root):
    profile = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    profile.set_led_color_hex("#123456")
    encoded = profile.to_bytes()
    reparsed = PfdProfile.from_bytes(encoded)
    assert reparsed.palette_color is None
    assert reparsed.led_color_hex == "#123456"
    assert reparsed.to_bytes() == encoded


def test_custom_color_setter_changes_only_ledcolor_and_preserves_tail(custom_path):
    profile = PfdProfile.from_path(custom_path)
    excluded = (profile.group_section, "LedColor1")
    before = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    dpi_before = profile.dpi_stage_struct
    profile.set_led_color_hex("#123456")
    after = {
        (section, key): value
        for section in profile.document.sections
        for key, value in profile.document.items(section)
        if (section, key) != excluded
    }
    assert after == before
    assert profile.led_color_raw == 0x00563412
    assert profile.dpi_stage_struct == dpi_before
    assert profile.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"


def test_custom_sample_diff_is_color_plus_resave_tail(project_root, custom_path):
    baseline = PfdProfile.from_path(
        project_root / "ProfilesMacros" / "Fixed" / "NM00_Base.pfd")
    custom = PfdProfile.from_path(custom_path)
    group = baseline.group_section
    isolated = {"LedColor1", "DPIStageValue"}
    assert {
        key: value for key, value in baseline.document.items(group)
        if key not in isolated
    } == {
        key: value for key, value in custom.document.items(group)
        if key not in isolated
    }
    content = custom_path.read_bytes()
    token = b"LedColor1="
    start = content.index(token) + len(token)
    end = content.index(b"\r\n", start)
    assert start == 277
    assert content[start:end] == b"1193046"
    assert custom.dpi_stage_struct.data[298:301] == b"\x0c\x48\x7f"
    assert custom.dpi_stage_struct.checksum == 0xC5
