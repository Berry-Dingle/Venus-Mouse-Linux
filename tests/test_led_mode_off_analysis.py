from __future__ import annotations

from pathlib import Path

from venus_profile.led_mode_off_analysis import (
    CAPTURE, POST_READ, PRE_READ, analyse_capture, compare_reads,
)


def test_true_off_reads_are_complete_and_only_mode_bytes_change():
    result = compare_reads(PRE_READ, POST_READ)
    assert result["read_count"] == 193
    assert result["returned_byte_count"] == 4604
    assert result["identical_coverage"]
    assert result["identical_presence_map"]
    assert [(x["address_hex"], x["previous_hex"], x["new_hex"])
            for x in result["changed_addresses"]] == [
        ("0x044C", "01", "00"), ("0x044E", "01", "00")
    ]
    assert result["pre_state"] == "7b 2d d2 01 00 01 03"
    assert result["post_state"] == "7b 2d d2 00 00 00 03"


def test_capture_has_matching_preview_and_apply_off_blocks():
    result = analyse_capture(CAPTURE)
    assert result["device_address"] == 16
    assert result["counts"]["f2_reads"] == 193
    blocks = result["mode_writes"]
    assert [(x["phase"], x["address_hex"], x["payload"]) for x in blocks] == [
        ("preview", "0x0449", "7b 2d d2 00 00 00"),
        ("preview", "0x044F", "03"),
        ("apply", "0x0449", "7b 2d d2 00 00 00"),
        ("apply", "0x044F", "03"),
    ]
    assert result["preview_and_apply_agree"]
    assert result["colour_unchanged"]
    assert result["brightness_unchanged"]
    assert len(result["f1_commands"]) == 9
    assert len(result["f5_commands"]) == 4


def test_off_analysis_outputs_are_deterministic():
    first = analyse_capture(CAPTURE)
    second = analyse_capture(CAPTURE)
    assert first == second
