from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.led_colour_custom_second_analysis import (
    CAPTURE, POST_READ, PRE_READ, SEMANTIC_RGB, TARGET_PREFIX,
    analyse_second_custom_colour_capture, write_analysis_outputs,
)
from venus_profile.lighting_state import inspect_live_read_lighting_state


@pytest.fixture(scope="module")
def analysis():
    return analyse_second_custom_colour_capture()


def test_metadata_coverage_and_exact_changes(analysis):
    comparison = analysis["read_comparison"]
    for key in ("pre", "post"):
        item = comparison[key]
        assert item["device"]["vendor_id"] == "04d9"
        assert item["device"]["product_id"] == "fc55"
        assert item["device"]["interface_number"] == "02"
        assert item["device"]["usage_page"] == "0xFFA0"
        assert item["device"]["usage"] == 1
        assert item["read_count"] == 193
        assert item["returned_byte_count"] == 4604
    assert comparison["identical_coverage"]
    assert comparison["identical_presence_map"]
    assert comparison["pre"]["presence_sha256"] == comparison["post"][
        "presence_sha256"
    ]
    assert [
        (item["address"], item["previous"], item["new"])
        for item in comparison["changed_addresses"]
    ] == [
        (0x0449, 0xD2, 0xE4),
        (0x044A, 0x2D, 0xA7),
        (0x044B, 0x7B, 0x12),
    ]


def test_exact_second_custom_bgr_and_prefix(analysis):
    field = analysis["colour_field"]
    assert SEMANTIC_RGB == (18, 167, 228)
    assert TARGET_PREFIX == bytes.fromhex("e4 a7 12 01 00 01")
    assert field["target_custom_hex"] == "#12A7E4"
    assert field["target_device_bytes"] == "e4 a7 12"
    assert field["target_prefix"] == "e4 a7 12 01 00 01"
    assert field["windows_picker_channel_transform"] == (
        "entered RRGGBB -> written BBGGRR"
    )
    assert field["native_device_channel_order"] == "R G B"
    assert field["later_live_test_disproved_native_bgr_rule"] is True
    assert field["arbitrary_24bit_writer_evidence_sufficient"] is True
    assert field["live_arbitrary_writer_enabled"] is False


def test_picker_intermediates_final_preview_and_apply(analysis):
    writes = analysis["prefix_covering_writes"]
    assert [(item["submission_frame"], item["data"]) for item in writes] == [
        (2061, "01 00 00 01 00 01"),
        (2089, "12 00 00 01 00 01"),
        (2119, "2a 01 00 01 00 01"),
        (2145, "2a 01 00 01 00 01"),
        (2173, "a7 12 00 01 00 01"),
        (2209, "7e 2a 01 01 00 01"),
        (2235, "7e 2a 01 01 00 01"),
        (2263, "e4 a7 12 01 00 01"),
        (3490, "e4 a7 12 01 00 01"),
    ]
    exact = "02 f3 49 04 06 00 00 00 e4 a7 12 01 00 01 00 00"
    assert writes[-2]["complete_payload"] == exact
    assert writes[-1]["complete_payload"] == exact


def test_apply_structure_final_commands_and_macro_untouched(analysis):
    apply = analysis["apply_comparison"]
    assert apply["same_146_block_596_byte_plan"]
    assert apply["same_apply_address_length_plan_as_first_custom"]
    assert apply["each_preview_cycle_matches_first_custom_narrow_plan"]
    assert apply["macro_region_touched"] is False
    assert [
        (item["address"], item["previous"], item["written"])
        for item in apply["changed_from_immediate_pre_read"]
    ] == [
        (0x0449, 0xD2, 0xE4),
        (0x044A, 0x2D, 0xA7),
        (0x044B, 0x7B, 0x12),
    ]
    assert [
        (item["submission_frame"], item["payload"])
        for item in analysis["final_commands"]
    ] == [
        (3484, "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3526, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3528, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3580, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3584, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3786, "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3788, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3790, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3792, "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3794, "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]


def test_post_read_persistence_and_general_custom_decode():
    pre = inspect_live_read_lighting_state(PRE_READ).led_color
    post = inspect_live_read_lighting_state(POST_READ).led_color
    assert pre.semantic_value == "Device RGB #D22D7B"
    assert pre.semantic_rgb == (210, 45, 123)
    assert post.raw_bytes_hex == "e4 a7 12"
    assert post.semantic_value == "Device RGB #E4A712"
    assert post.semantic_rgb == (228, 167, 18)
    assert post.arbitrary_values_supported is True
    assert 0xE4A712 not in post.live_write_supported_values


def test_unknown_custom_bytes_decode_by_confirmed_bgr_rule():
    # A third value is not claimed as captured; this only verifies the
    # semantic decoder applies the now-confirmed general channel transform.
    from venus_profile.lighting_state import _device_colour_field

    field = _device_colour_field(bytes.fromhex("56 34 12"), "unit-test")
    assert field.semantic_value == "Device RGB #563412"
    assert field.semantic_rgb == (0x56, 0x34, 0x12)
    assert field.live_write_supported_values == (
        0xFF0000, 0x0E0FFF, 0x00FF01
    )


def test_outputs_are_deterministic(tmp_path: Path, analysis):
    first, second = tmp_path / "first", tmp_path / "second"
    write_analysis_outputs(first, analysis=analysis)
    write_analysis_outputs(second, analysis=analysis)
    names = {
        "colour_transactions.json", "colour_write_blocks.tsv",
        "pre_vs_post_diff.tsv", "preview_sequence.md",
        "apply_sequence.md", "custom_colour_protocol_report.md",
    }
    assert {item.name for item in first.iterdir()} == names
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert json.loads(
        (first / "colour_transactions.json").read_text()
    )["capture"] == str(CAPTURE)


def test_no_arbitrary_live_writer_added():
    root = Path(__file__).parents[1] / "src" / "venus_profile"
    analysis_source = (
        root / "led_colour_custom_second_analysis.py"
    ).read_text()
    guarded_source = (root / "guarded_led_colour.py").read_text()
    for source in (analysis_source,):
        assert "discover_venus_hidraw" not in source
        assert "/dev/hidraw" not in source
        assert "_hid_iocsfeature" not in source
    assert "set_custom" not in guarded_source
    assert "write_custom" not in guarded_source
    assert "--custom" not in guarded_source
