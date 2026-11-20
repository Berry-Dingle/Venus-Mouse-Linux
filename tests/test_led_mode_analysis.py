from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.led_mode_analysis import (
    CAPTURE,
    POST_READ,
    PRE_READ,
    analyse_led_mode_capture,
    compare_live_reads,
    write_analysis_outputs,
)
from venus_profile.lighting_state import inspect_live_read_lighting_state


@pytest.fixture(scope="module")
def analysis():
    return analyse_led_mode_capture()


def test_pre_post_live_read_metadata_and_coverage():
    comparison = compare_live_reads()
    for key in ("pre", "post"):
        item = comparison[key]
        assert item["device"]["vendor_id"] == "04d9"
        assert item["device"]["product_id"] == "fc55"
        assert item["device"]["interface_number"] == "02"
        assert item["read_count"] == 193
        assert item["returned_byte_count"] == 4604
    assert comparison["identical_coverage"]
    assert comparison["identical_presence_map"]
    assert (
        comparison["pre"]["presence_sha256"]
        == comparison["post"]["presence_sha256"]
        == "477f8479225ecd3f9656ad75d506ab02"
        "a0231e4eca281c4d21f85a1f07ab8dc2"
    )


def test_exact_persistent_change_set():
    changes = compare_live_reads()["changed_addresses"]
    assert [
        (item["address"], item["previous"], item["new"])
        for item in changes
    ] == [(0x044C, 0x02, 0x01), (0x044D, 0x05, 0x00)]


def test_preview_and_apply_mode_writes(analysis):
    writes = analysis["mode_affecting_writes"]
    assert [
        (
            item["submission_frame"],
            item["completion_frame"],
            item["address"],
            item["block_length"],
            item["data"],
            item["complete_payload"],
        )
        for item in writes
    ] == [
        (
            5897, 5898, 0x0449, 6, "ff 00 00 01 00 01",
            "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00",
        ),
        (
            5954, 5955, 0x0449, 6, "ff 00 00 01 00 01",
            "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00",
        ),
    ]


def test_apply_plan_final_commands_and_no_macro_write(analysis):
    comparison = analysis["brightness_apply_comparison"]
    assert comparison["same_146_block_address_length_plan"]
    assert comparison["same_596_byte_sparse_write_size"]
    assert comparison["macro_region_touched"] is False
    assert [
        (item["submission_frame"], item["payload"])
        for item in analysis["final_commands"]
    ] == [
        (5948, "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"),
        (5990, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (5992, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6044, "02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6048, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6250, "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6252, "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6254, "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6256, "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00"),
        (6258, "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"),
    ]


def test_apply_changed_from_immediate_pre_read_only_two_bytes(analysis):
    assert [
        (item["address"], item["previous"], item["written"])
        for item in analysis["brightness_apply_comparison"][
            "changed_from_immediate_pre_read"
        ]
    ] == [(0x044C, 2, 1), (0x044D, 5, 0)]


def test_post_read_semantics_and_write_remains_disabled():
    state = inspect_live_read_lighting_state(POST_READ)
    assert state.led_mode.device_memory_address == 0x044C
    assert state.led_mode.raw_value == 1
    assert state.led_mode.semantic_value == "Full Brightness"
    assert state.led_mode.live_read_supported
    assert state.led_mode.live_write_supported_values == ()


def test_analysis_outputs_are_deterministic(tmp_path: Path, analysis):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_analysis_outputs(first, analysis=analysis)
    write_analysis_outputs(second, analysis=analysis)
    names = {
        "led_mode_transactions.json",
        "led_mode_write_blocks.tsv",
        "pre_vs_post_diff.tsv",
        "preview_sequence.md",
        "apply_sequence.md",
        "led_mode_protocol_report.md",
    }
    assert {item.name for item in first.iterdir()} == names
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    parsed = json.loads((first / "led_mode_transactions.json").read_text())
    assert parsed["capture"] == str(CAPTURE)


def test_offline_analyser_has_no_device_or_live_write_path():
    module = (
        Path(__file__).parents[1] / "src" / "venus_profile"
        / "led_mode_analysis.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "DeviceSession", "discover_venus_hidraw", "/dev/hidraw",
        "set_feature_report", "write_memory",
    )
    assert all(term not in module for term in forbidden)
    assert PRE_READ.is_dir() and POST_READ.is_dir()
