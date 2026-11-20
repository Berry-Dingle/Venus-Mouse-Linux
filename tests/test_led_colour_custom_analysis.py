from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.led_colour_custom_analysis import (
    CAPTURE, CUSTOM_PREFIX, ENTERED_RGB, POST_READ, PRE_READ,
    analyse_custom_colour_capture, write_analysis_outputs,
)
from venus_profile.lighting_state import inspect_live_read_lighting_state


@pytest.fixture(scope="module")
def analysis():
    return analyse_custom_colour_capture()


def test_metadata_coverage_and_exact_changes(analysis):
    assert analysis["phases"]["usb_setup"] == {
        "first_frame": 1202, "last_frame": 1281
    }
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
        (0x0449, 0x00, 0xD2),
        (0x044A, 0xFF, 0x2D),
        (0x044B, 0x01, 0x7B),
    ]


def test_windows_picker_reversal_is_separate_from_native_rgb(analysis):
    field = analysis["colour_field"]
    assert ENTERED_RGB == (123, 45, 210)
    assert CUSTOM_PREFIX == bytes.fromhex("d2 2d 7b 01 00 01")
    assert field["expected_if_direct_rgb"] == "7b 2d d2"
    assert field["observed_device_bytes"] == "d2 2d 7b"
    assert field["observed_full_prefix"] == "d2 2d 7b 01 00 01"
    assert field["windows_picker_channel_transform"] == (
        "entered RRGGBB -> written BBGGRR"
    )
    assert field["native_device_channel_order"] == "R G B"
    assert field["visible_native_rgb"] == [210, 45, 123]
    assert field["windows_picker_entered_rgb"] == [123, 45, 210]
    assert field["arbitrary_custom_rgb_storage_confirmed"] is True
    assert field["direct_rgb_storage_confirmed_by_later_live_visible_test"] is True
    assert field["all_palette_entries_need_individual_testing"] is False


def test_picker_intermediates_final_preview_and_apply(analysis):
    writes = analysis["prefix_covering_writes"]
    assert len(writes) == 9
    assert [(item["submission_frame"], item["data"]) for item in writes] == [
        (2121, "07 00 00 01 00 01"),
        (2153, "7b 00 00 01 00 01"),
        (2177, "7b 00 00 01 00 01"),
        (2205, "b2 07 00 01 00 01"),
        (2237, "2d 7b 00 01 00 01"),
        (2265, "dd b2 07 01 00 01"),
        (2289, "dd b2 07 01 00 01"),
        (2323, "d2 2d 7b 01 00 01"),
        (2374, "d2 2d 7b 01 00 01"),
    ]
    assert writes[-2]["complete_payload"] == (
        "02 f3 49 04 06 00 00 00 d2 2d 7b 01 00 01 00 00"
    )
    assert writes[-1]["complete_payload"] == writes[-2]["complete_payload"]


def test_apply_plan_final_commands_and_macro_untouched(analysis):
    apply = analysis["apply_comparison"]
    assert apply["same_146_block_596_byte_plan"]
    assert apply["same_apply_address_length_plan_as_prior_colour"]
    assert apply["each_preview_cycle_matches_prior_narrow_plan"]
    assert apply["macro_region_touched"] is False
    assert [
        (item["address"], item["previous"], item["written"])
        for item in apply["changed_from_immediate_pre_read"]
    ] == [
        (0x0449, 0x00, 0xD2),
        (0x044A, 0xFF, 0x2D),
        (0x044B, 0x01, 0x7B),
    ]
    assert [
        (item["submission_frame"], item["payload"])
        for item in analysis["final_commands"]
    ] == [
        (2368, "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2410, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2412, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2464, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2468, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2670, "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2672, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2674, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2676, "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00"),
        (2678, "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]


def test_post_read_persistence_and_semantic_decode():
    pre = inspect_live_read_lighting_state(PRE_READ).led_color
    post = inspect_live_read_lighting_state(POST_READ).led_color
    assert pre.semantic_value == "Official solid green"
    assert pre.semantic_rgb == (0, 255, 1)
    assert post.raw_bytes_hex == "d2 2d 7b"
    assert post.semantic_value == "Device RGB #D22D7B"
    assert post.semantic_rgb == (210, 45, 123)
    assert post.arbitrary_values_supported is True
    assert 0xD22D7B not in post.live_write_supported_values


def test_outputs_deterministic(tmp_path: Path, analysis):
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


def test_offline_analysis_adds_no_arbitrary_live_writer():
    root = Path(__file__).parents[1] / "src" / "venus_profile"
    analysis_source = (root / "led_colour_custom_analysis.py").read_text()
    lighting_source = (root / "lighting_state.py").read_text()
    for source in (analysis_source, lighting_source):
        assert "discover_venus_hidraw" not in source
        assert "/dev/hidraw" not in source
        assert "_hid_iocsfeature" not in source
        assert "set_custom" not in source
        assert "write_custom" not in source
