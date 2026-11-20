from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from venus_profile.offline_usb_capture import (
    analyse_feature_capture,
    reconstruct_memory,
    reconstruct_written_memory,
)
from evidence_paths import REPO  # noqa: E402


@pytest.fixture(scope="module")
def brightness_capture(project_root: Path):
    path = (
        project_root / "usb-captures"
        / "brightness_one_step_apply_20260727_082346.pcapng"
    )
    return analyse_feature_capture(path)


@pytest.fixture(scope="module")
def apply_writes(brightness_capture):
    _, _, writes = brightness_capture
    return [
        write for write in writes
        if 10645 <= write.transfer.submission_frame <= 10943
    ]


@pytest.fixture(scope="module")
def factory_bytes(project_root: Path) -> dict[int, int]:
    path = (
        project_root / "usb-captures" / "analysis"
        / "factory_memory_map.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    return {int(key, 16): value for key, value in document["bytes"].items()}


def test_capture_commands_and_phase_boundaries(brightness_capture):
    transfers, reads, writes = brightness_capture
    sets = [item for item in transfers if item.direction == "SET_REPORT"]
    assert Counter(item.payload[1] for item in sets) == {
        0xF1: 9,
        0xF2: 193,
        0xF3: 157,
        0xF5: 4,
    }
    assert len(reads) == 193
    assert len(writes) == 157
    by_frame = {item.submission_frame: item for item in sets}
    assert by_frame[3124].payload[:3] == bytes.fromhex("02 f5 00")
    assert by_frame[3900].completion_frame == 3901
    assert by_frame[10643].payload[:3] == bytes.fromhex("02 f5 00")
    assert by_frame[10953].completion_frame == 10954
    assert by_frame[10953].payload[:3] == bytes.fromhex("02 f5 01")


def test_apply_f3_boundaries_and_counts(apply_writes):
    assert len(apply_writes) == 146
    assert apply_writes[0].transfer.submission_frame == 10645
    assert apply_writes[0].transfer.completion_frame == 10646
    assert apply_writes[-1].transfer.submission_frame == 10943
    assert apply_writes[-1].transfer.completion_frame == 10944
    assert sum(write.block_length for write in apply_writes) == 596


def test_f3_report_ids_transfer_sizes_and_payload_sizes(apply_writes):
    assert Counter(
        (
            write.transfer.report_id,
            write.transfer.requested_report_length,
            write.transfer.completion_data_length,
        )
        for write in apply_writes
    ) == {
        (2, 16, 16): 145,
        (3, 64, 64): 1,
    }
    assert Counter(write.block_length for write in apply_writes) == {
        1: 5,
        2: 8,
        4: 101,
        5: 25,
        6: 6,
        10: 1,
    }
    assert not any(
        write.transfer.report_id in {4, 5} for write in apply_writes
    )


def test_f3_header_address_payload_and_padding_decode(apply_writes):
    write = next(
        item for item in apply_writes
        if item.transfer.submission_frame == 10649
    )
    assert write.transfer.payload == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 ff 00 00 02 05 01 00 00"
    )
    assert write.address == 0x0449
    assert write.block_length == 6
    assert write.header_reserved == b"\x00\x00"
    assert write.data == bytes.fromhex("ff 00 00 02 05 01")
    assert write.padding == b"\x00\x00"
    assert write.validate() == []


def test_report3_is_64_bytes_not_a_report4_or_1024_byte_write(apply_writes):
    writes = [
        item for item in apply_writes if item.transfer.report_id == 3
    ]
    assert len(writes) == 1
    write = writes[0]
    assert write.transfer.submission_frame == 10673
    assert write.transfer.requested_report_length == 64
    assert write.block_length == 10
    assert write.data == bytes.fromhex(
        "01 00 01 00 01 00 01 00 01 00"
    )
    assert write.padding == b"\x00" * 46


def test_written_memory_reconstruction_is_deterministic(apply_writes):
    memory = reconstruct_written_memory(apply_writes)
    again = reconstruct_written_memory(reversed(apply_writes))
    assert memory.values == again.values
    assert memory.overlaps == []
    assert memory.minimum_address == 0x0020
    assert memory.maximum_address == 0x046F
    assert len(memory.values) == 596
    assert len(memory.flat_bytes()) == 1104
    assert hashlib.sha256(memory.flat_bytes()).hexdigest() == (
        "45e319cf925675effaae513c1868f9e26ba7a25b9ef2dd9aae01ec1b8b1b96bf"
    )


def test_initial_read_matches_factory_capture_exactly(
    brightness_capture, factory_bytes
):
    _, reads, _ = brightness_capture
    memory = reconstruct_memory(reads)
    assert memory.values == factory_bytes
    assert hashlib.sha256(memory.flat_bytes()).hexdigest() == (
        "fa304c2aaa332e2b18491c11ed4a2d6527948114fa75a8aeb271b0ed09abf1a8"
    )


def test_only_known_changed_address_is_brightness(
    apply_writes, factory_bytes
):
    memory = reconstruct_written_memory(apply_writes)
    changes = {
        address: (factory_bytes[address], value)
        for address, value in memory.values.items()
        if address in factory_bytes and factory_bytes[address] != value
    }
    assert changes == {0x044F: (0x02, 0x03)}
    assert sum(address in factory_bytes for address in memory.values) == 579
    assert len(memory.values) - len(changes) - 17 == 578


def test_brightness_preview_and_apply_write_same_change(brightness_capture):
    _, _, writes = brightness_capture
    preview = [
        item for item in writes
        if 6356 <= item.transfer.submission_frame <= 6376
    ]
    apply = [
        item for item in writes
        if 10645 <= item.transfer.submission_frame <= 10943
    ]
    assert len(preview) == 11
    assert next(item.data for item in preview if item.address == 0x044F) == b"\x03"
    assert next(item.data for item in apply if item.address == 0x044F) == b"\x03"
    assert {
        address: next(item.data for item in preview if item.address == address)
        for address in (0x0457, 0x045F, 0x0467, 0x046F)
    } == {
        0x0457: b"\x02",
        0x045F: b"\x02",
        0x0467: b"\x02",
        0x046F: b"\x02",
    }


def test_no_macro_writes_and_no_post_apply_verification(
    brightness_capture, apply_writes
):
    transfers, _, _ = brightness_capture
    assert all(write.address + write.block_length <= 0x0478 for write in apply_writes)
    assert not [
        item for item in transfers
        if item.submission_frame > 10954 and item.direction == "GET_REPORT"
    ]
    assert not [
        item for item in transfers
        if item.submission_frame > 3900
        and item.direction == "SET_REPORT"
        and len(item.payload) > 1
        and item.payload[1] == 0xF2
    ]


def test_final_commands_and_successful_transport_completions(
    brightness_capture,
):
    transfers, _, _ = brightness_capture
    by_frame = {
        item.submission_frame: item
        for item in transfers if item.direction == "SET_REPORT"
    }
    expected = {
        10945: "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00",
        10947: "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00",
        10949: "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00",
        10951: "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00",
        10953: "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00",
    }
    assert {
        frame: by_frame[frame].payload.hex(" ") for frame in expected
    } == expected
    assert all(by_frame[frame].completion_status == 0 for frame in expected)


def test_f3_has_no_transmitted_checksum_field(apply_writes):
    assert all(write.header_reserved == b"\x00\x00" for write in apply_writes)
    assert all(not any(write.padding) for write in apply_writes)
    assert all(len(write.data) == write.block_length for write in apply_writes)


def test_parser_has_no_live_hid_access(project_root: Path):
    source = (
        REPO / "src" / "venus_profile"
        / "offline_usb_capture.py"
    ).read_text(encoding="utf-8").lower()
    assert "/dev/hidraw" not in source
    assert "hidd_setfeature" not in source
    assert "open_hid" not in source
    assert "pyusb" not in source
