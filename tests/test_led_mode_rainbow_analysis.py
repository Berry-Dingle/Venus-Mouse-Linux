from pathlib import Path

from venus_profile.led_mode_rainbow_analysis import (
    CAPTURE,
    POST_READ,
    PRE_READ,
    analyse_capture,
    compare_reads,
)


def test_rainbow_reads_have_identical_coverage_and_only_mode_bytes_change():
    result = compare_reads(PRE_READ, POST_READ)
    assert result["identical_coverage"] is True
    assert result["identical_presence_map"] is True
    assert result["read_count"] == 193
    assert result["returned_byte_count"] == 4604
    assert [(item["address_hex"], item["previous_hex"], item["new_hex"])
            for item in result["changed_addresses"]] == [
                ("0x044C", "01", "02"),
                ("0x044E", "01", "03"),
            ]
    assert result["device"]["vendor_id"] == "04d9"
    assert result["device"]["product_id"] == "fc55"
    assert result["device"]["interface_number"] == "02"


def test_rainbow_preview_and_apply_write_exact_same_prefix():
    result = analyse_capture(CAPTURE, pre_directory=PRE_READ,
                             post_directory=POST_READ)
    assert result["counts"] == {
        "feature_transfers": 556,
        "f2_reads": 193,
        "f3_writes": 157,
        "commands": {"0xF5": 4, "0xF2": 193, "0xF3": 157, "0xF1": 9},
    }
    mode_writes = result["mode_writes"]
    assert [(item["frame"], item["phase"], item["address_hex"],
             item["payload"]) for item in mode_writes] == [
        (3289, "preview", "0x0449", "7b 2d d2 02 00 03"),
        (3291, "preview", "0x044F", "03"),
        (3340, "apply", "0x0449", "7b 2d d2 02 00 03"),
        (3342, "apply", "0x044F", "03"),
    ]
    assert result["rainbow_prefix"] == "7b 2d d2 02 00 03"
    assert result["preview_and_apply_agree"] is True
    assert result["colour_unchanged"] is True
    assert result["brightness_unchanged"] is True


def test_rainbow_analysis_is_fixture_only_and_deterministic():
    first = analyse_capture(CAPTURE, pre_directory=PRE_READ,
                             post_directory=POST_READ)
    second = analyse_capture(CAPTURE, pre_directory=PRE_READ,
                              post_directory=POST_READ)
    assert first == second
    assert Path(CAPTURE).is_file()
