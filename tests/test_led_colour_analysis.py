from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.led_colour_analysis import (
    BLUE_DEVICE,
    CAPTURE,
    POST_READ,
    PRE_READ,
    RED_DEVICE,
    analyse_led_colour_capture,
    compare_live_reads,
    write_analysis_outputs,
)
from venus_profile.lighting_state import (
    LED_COLOUR_ADDRESS_PROFILE1,
    LED_COLOUR_LENGTH_PROFILE1,
    inspect_live_read_lighting_state,
)


@pytest.fixture(scope="module")
def analysis():
    return analyse_led_colour_capture()


def test_pre_post_metadata_coverage_and_presence():
    result = compare_live_reads()
    for key in ("pre", "post"):
        item = result[key]
        assert item["device"]["vendor_id"] == "04d9"
        assert item["device"]["product_id"] == "fc55"
        assert item["device"]["interface_number"] == "02"
        assert item["device"]["usage_page"] == "0xFFA0"
        assert item["device"]["usage"] == 1
        assert item["read_count"] == 193
        assert item["returned_byte_count"] == 4604
    assert result["identical_coverage"]
    assert result["identical_presence_map"]
    assert result["pre"]["presence_sha256"] == result["post"][
        "presence_sha256"
    ]


def test_exact_changed_address_set_and_raw_colours():
    result = compare_live_reads()
    assert [
        (item["address"], item["previous"], item["new"])
        for item in result["changed_addresses"]
    ] == [
        (0x0449, 0xFF, 0x0E),
        (0x044A, 0x00, 0x0F),
        (0x044B, 0x00, 0xFF),
    ]
    assert bytes.fromhex(result["pre_colour"]) == RED_DEVICE
    assert bytes.fromhex(result["post_colour"]) == BLUE_DEVICE


def test_device_colour_field_and_byte_order(analysis):
    field = analysis["colour_field"]
    assert field["address"] == 0x0449
    assert field["length"] == 3
    assert field["byte_order"] == "R G B"
    assert field["red_device_bytes"] == "ff 00 00"
    assert field["red_device_rgb"] == [255, 0, 0]
    assert field["blue_device_bytes"] == "0e 0f ff"
    assert field["blue_device_rgb"] == [14, 15, 255]
    assert field["pfd_stored_unchanged"] is False
    assert field["arbitrary_rgb_supported_by_evidence"] is False


def test_preview_and_apply_colour_reports(analysis):
    writes = analysis["colour_covering_writes"]
    assert [
        (
            item["submission_frame"], item["completion_frame"],
            item["timestamp"], item["report_id"],
            item["declared_transfer_length"], item["address"],
            item["block_length"], item["data"], item["complete_payload"],
        )
        for item in writes
    ] == [
        (
            8208, 8209, "2026-07-27T11:19:04.725840000Z",
            2, 16, 0x0449, 6, "0e 0f ff 01 00 01",
            "02 f3 49 04 06 00 00 00 0e 0f ff 01 00 01 00 00",
        ),
        (
            8248, 8249, "2026-07-27T11:19:23.383343000Z",
            2, 16, 0x0449, 6, "0e 0f ff 01 00 01",
            "02 f3 49 04 06 00 00 00 0e 0f ff 01 00 01 00 00",
        ),
    ]


def test_apply_plan_final_commands_and_macro_untouched(analysis):
    apply = analysis["apply_comparison"]
    assert apply["same_146_block_596_byte_plan"]
    assert apply["prior_captures"]["brightness"]["same_address_length_plan"]
    assert apply["prior_captures"]["led_mode"]["same_address_length_plan"]
    assert apply["macro_region_touched"] is False
    assert [
        (item["address"], item["previous"], item["written"])
        for item in apply["changed_from_immediate_pre_read"]
    ] == [
        (0x0449, 0xFF, 0x0E),
        (0x044A, 0x00, 0x0F),
        (0x044B, 0x00, 0xFF),
    ]
    assert [
        (item["submission_frame"], item["payload"])
        for item in analysis["final_commands"]
    ] == [
        (8242, "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8284, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8286, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8338, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8342, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8544, "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8546, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8548, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8550, "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00"),
        (8552, "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]


def test_lighting_state_decodes_pre_red_and_post_blue():
    red = inspect_live_read_lighting_state(PRE_READ).led_color
    blue = inspect_live_read_lighting_state(POST_READ).led_color
    assert red.device_memory_address == LED_COLOUR_ADDRESS_PROFILE1
    assert red.device_memory_length == LED_COLOUR_LENGTH_PROFILE1
    assert red.raw_bytes_hex == "ff 00 00"
    assert red.semantic_rgb == (255, 0, 0)
    assert red.semantic_value == "Official solid red"
    assert blue.raw_bytes_hex == "0e 0f ff"
    assert blue.semantic_rgb == (14, 15, 255)
    assert blue.semantic_value == "Official solid blue"
    for field in (red, blue):
        assert field.live_read_supported
        assert field.live_write_supported_values == (
            0xFF0000, 0x0E0FFF, 0x00FF01
        )
        assert field.arbitrary_values_supported is True


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


def test_analyser_and_semantic_model_add_no_live_colour_writer():
    source_root = Path(__file__).parents[1] / "src" / "venus_profile"
    analysis_source = (source_root / "led_colour_analysis.py").read_text()
    lighting_source = (source_root / "lighting_state.py").read_text()
    forbidden = (
        "discover_venus_hidraw", "/dev/hidraw", "_hid_iocsfeature",
        "set_led_colour", "write_led_colour", "send_colour",
    )
    assert all(term not in analysis_source for term in forbidden)
    assert all(term not in lighting_source for term in forbidden)
