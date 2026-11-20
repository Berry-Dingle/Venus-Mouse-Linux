from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.led_colour_blue_green_analysis import (
    CAPTURE, GREEN_DEVICE, GREEN_PREFIX, POST_READ, PRE_READ,
    TRANSIENT_PREVIEW_PREFIX, analyse_blue_to_green_capture,
    write_analysis_outputs,
)
from venus_profile.lighting_state import inspect_live_read_lighting_state


@pytest.fixture(scope="module")
def analysis():
    return analyse_blue_to_green_capture()


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
        (0x0449, 0x0E, 0x00),
        (0x044A, 0x0F, 0xFF),
        (0x044B, 0xFF, 0x01),
    ]


def test_green_direct_rgb_and_complete_prefix(analysis):
    field = analysis["colour_field"]
    assert GREEN_DEVICE == bytes.fromhex("00 ff 01")
    assert GREEN_PREFIX == bytes.fromhex("00 ff 01 01 00 01")
    assert field["green_bytes"] == "00 ff 01"
    assert field["green_rgb"] == [0, 255, 1]
    assert field["green_prefix"] == "00 ff 01 01 00 01"
    assert field["byte_order"] == "R G B"
    assert field["direct_rgb_consistent_across_red_blue_green"] is True
    assert field["arbitrary_rgb_write_supported"] is False


def test_all_prefix_writes_transient_selected_and_apply(analysis):
    writes = analysis["prefix_covering_writes"]
    assert len(writes) == 3
    assert bytes.fromhex(writes[0]["data"]) == TRANSIENT_PREVIEW_PREFIX
    assert [
        (
            item["submission_frame"], item["completion_frame"],
            item["timestamp"], item["report_id"],
            item["declared_transfer_length"], item["address"],
            item["block_length"], item["complete_payload"],
        )
        for item in writes
    ] == [
        (
            2922, 2923, "2026-07-27T11:48:47.504390000Z",
            2, 16, 0x0449, 6,
            "02 f3 49 04 06 00 00 00 02 b5 34 01 00 01 00 00",
        ),
        (
            2946, 2947, "2026-07-27T11:48:50.980160000Z",
            2, 16, 0x0449, 6,
            "02 f3 49 04 06 00 00 00 00 ff 01 01 00 01 00 00",
        ),
        (
            3007, 3008, "2026-07-27T11:49:19.185260000Z",
            2, 16, 0x0449, 6,
            "02 f3 49 04 06 00 00 00 00 ff 01 01 00 01 00 00",
        ),
    ]


def test_preview_apply_plans_final_commands_and_macro_untouched(analysis):
    comparison = analysis["apply_comparison"]
    assert comparison["same_146_block_596_byte_plan"]
    assert comparison["same_apply_address_length_plan_as_red_to_blue"]
    assert comparison["each_preview_cycle_matches_prior_narrow_plan"]
    assert comparison["macro_region_touched"] is False
    assert [
        (item["address"], item["previous"], item["written"])
        for item in comparison["changed_from_immediate_pre_read"]
    ] == [
        (0x0449, 0x0E, 0x00),
        (0x044A, 0x0F, 0xFF),
        (0x044B, 0xFF, 0x01),
    ]
    assert [
        (item["submission_frame"], item["payload"])
        for item in analysis["final_commands"]
    ] == [
        (3001, "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3043, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3045, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3097, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3101, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3303, "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3305, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3307, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3309, "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00"),
        (3311, "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]


def test_post_read_persists_green_and_semantic_model_has_guarded_green_only():
    pre = inspect_live_read_lighting_state(PRE_READ).led_color
    post = inspect_live_read_lighting_state(POST_READ).led_color
    assert pre.semantic_value == "Official solid blue"
    assert pre.semantic_rgb == (14, 15, 255)
    assert post.semantic_value == "Official solid green"
    assert post.raw_bytes_hex == "00 ff 01"
    assert post.semantic_rgb == (0, 255, 1)
    assert post.live_read_supported
    assert post.live_write_supported_values == (
        0xFF0000, 0x0E0FFF, 0x00FF01
    )
    assert post.arbitrary_values_supported is True


def test_outputs_are_deterministic(tmp_path: Path, analysis):
    first, second = tmp_path / "first", tmp_path / "second"
    write_analysis_outputs(first, analysis=analysis)
    write_analysis_outputs(second, analysis=analysis)
    names = {
        "colour_transactions.json", "colour_write_blocks.tsv",
        "pre_vs_post_diff.tsv", "preview_sequence.md",
        "apply_sequence.md", "colour_protocol_report.md",
    }
    assert {item.name for item in first.iterdir()} == names
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert json.loads(
        (first / "colour_transactions.json").read_text()
    )["capture"] == str(CAPTURE)


def test_offline_analyser_and_model_add_no_green_writer():
    root = Path(__file__).parents[1] / "src" / "venus_profile"
    analysis_source = (
        root / "led_colour_blue_green_analysis.py"
    ).read_text()
    lighting_source = (root / "lighting_state.py").read_text()
    for source in (analysis_source, lighting_source):
        assert "discover_venus_hidraw" not in source
        assert "/dev/hidraw" not in source
        assert "_hid_iocsfeature" not in source
        assert "set_green" not in source
        assert "write_green" not in source
