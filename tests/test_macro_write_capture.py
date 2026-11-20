from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from venus_profile.actions import ActionType
from venus_profile.offline_usb_capture import (
    DeviceMacroAssignment,
    analyse_feature_capture,
    decode_device_macro_slot,
    reconstruct_memory,
    reconstruct_written_memory,
)
from evidence_paths import REPO  # noqa: E402


@pytest.fixture(scope="module")
def macro_capture(project_root: Path):
    path = (
        project_root / "usb-captures"
        / "macro_slot1_a_press_release_20260727_090056.pcapng"
    )
    return analyse_feature_capture(path)


@pytest.fixture(scope="module")
def macro_write(macro_capture):
    _, _, writes = macro_capture
    candidates = [write for write in writes if write.address >= 0x0478]
    assert len(candidates) == 1
    return candidates[0]


@pytest.fixture(scope="module")
def macro_slot(macro_write):
    return decode_device_macro_slot(
        macro_write.data, address=macro_write.address
    )


@pytest.fixture(scope="module")
def final_apply_writes(macro_capture):
    _, _, writes = macro_capture
    return [
        write for write in writes
        if 4372 <= write.transfer.submission_frame <= 4670
    ]


@pytest.fixture(scope="module")
def factory_bytes(project_root: Path):
    path = (
        project_root / "usb-captures" / "analysis"
        / "factory_memory_map.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    return {int(key, 16): value for key, value in document["bytes"].items()}


def test_three_f5_bracketed_cycles(macro_capture):
    transfers, reads, _ = macro_capture
    f5 = [
        item for item in transfers
        if item.direction == "SET_REPORT" and item.payload[1] == 0xF5
    ]
    assert [
        (item.submission_frame, item.completion_frame, item.payload[2])
        for item in f5
    ] == [
        (2836, 2837, 0),
        (3612, 3613, 1),
        (4258, 4259, 0),
        (4262, 4263, 1),
        (4370, 4371, 0),
        (4680, 4681, 1),
    ]
    assert len(reads) == 193
    assert all(
        2838 <= read.set_transfer.submission_frame <= 3608
        for read in reads
    )


def test_capture_command_counts(macro_capture):
    transfers, _, writes = macro_capture
    sets = [item for item in transfers if item.direction == "SET_REPORT"]
    assert Counter(item.payload[1] for item in sets) == {
        0xF1: 8,
        0xF2: 193,
        0xF3: 147,
        0xF5: 6,
    }
    assert len(writes) == 147


def test_macro_transport_is_one_256_byte_report4(macro_write):
    assert macro_write.transfer.submission_frame == 4260
    assert macro_write.transfer.completion_frame == 4261
    assert macro_write.transfer.report_id == 4
    assert macro_write.transfer.report_type == 3
    assert macro_write.transfer.interface_index == 2
    assert macro_write.transfer.requested_report_length == 256
    assert macro_write.transfer.completion_data_length == 256
    assert len(macro_write.transfer.payload) == 256
    assert macro_write.transfer.completion_status == 0
    assert macro_write.transfer.payload[:8] == bytes.fromhex(
        "04 f3 78 04 c8 00 00 00"
    )
    assert macro_write.address == 0x0478
    assert macro_write.block_length == 200
    assert len(macro_write.padding) == 48
    assert macro_write.padding == b"\x00" * 48


def test_only_ui_slot1_device_slot0_is_written(macro_capture, macro_write):
    _, _, writes = macro_capture
    macro_writes = [item for item in writes if item.address >= 0x0478]
    assert macro_writes == [macro_write]
    assert macro_write.address == 0x0478 + 0 * 0x00C8
    assert macro_write.address + macro_write.block_length - 1 == 0x053F


def test_macro_slot_action_records_include_editor_minimum_delay(macro_slot):
    assert macro_slot.populated_record_count == 3
    assert [record.raw for record in macro_slot.populated_records] == [
        bytes.fromhex("84 04 00"),
        bytes.fromhex("06 01 00"),
        bytes.fromhex("04 04 00"),
    ]
    assert macro_slot.populated_records[0].action_type == ActionType.KEYBOARD
    assert macro_slot.populated_records[0].state_down
    assert macro_slot.populated_records[0].usage == 0x04
    assert macro_slot.populated_records[1].action_type == ActionType.DELAY
    assert macro_slot.populated_records[1].delay_ms == 10
    assert macro_slot.populated_records[2].action_type == ActionType.KEYBOARD
    assert not macro_slot.populated_records[2].state_down
    assert macro_slot.populated_records[2].usage == 0x04


def test_device_slot_padding_and_file_checksum_is_not_transmitted(macro_slot):
    assert len(macro_slot.raw) == 200
    assert len(macro_slot.records) == 63
    assert all(
        record.raw == b"\x00\x00\x00" for record in macro_slot.records[3:]
    )
    assert macro_slot.trailing_bytes == b"\x00" * 11
    assert macro_slot.file_style_additive_checksum == 0x97
    assert macro_slot.byte_189 == 0x00
    assert not macro_slot.to_dict()["file_style_checksum_present"]


def test_macro_slot_reconstruction_is_deterministic(macro_slot):
    assert hashlib.sha256(macro_slot.raw).hexdigest() == (
        "e37581a2157c026fb8c2d8b5375cd4ff6d6c6083a62bc7bea9488b4919b1204b"
    )
    assert decode_device_macro_slot(
        bytes(macro_slot.raw), address=0x0478
    ) == macro_slot


def test_exact_macro_addresses_changed_from_factory(
    macro_slot, factory_bytes
):
    changes = {
        macro_slot.address + offset: (
            factory_bytes[macro_slot.address + offset], value
        )
        for offset, value in enumerate(macro_slot.raw)
        if factory_bytes[macro_slot.address + offset] != value
    }
    assert changes == {
        0x0478: (0x00, 0x84),
        0x0479: (0x00, 0x04),
        0x047B: (0x00, 0x06),
        0x047C: (0x00, 0x01),
        0x047E: (0x00, 0x04),
        0x047F: (0x00, 0x04),
    }


def test_side_button1_assignment_uses_default_single_execution(
    final_apply_writes,
):
    memory = reconstruct_written_memory(final_apply_writes)
    raw = bytes(memory.values[address] for address in range(0x009A, 0x009E))
    assignment = DeviceMacroAssignment(raw)
    assert raw == bytes.fromhex("91 00 01 00")
    assert assignment.function_code == 0x91
    assert assignment.slot_index == 0
    assert not assignment.while_held
    assert not assignment.toggle
    assert assignment.execution_count == 1


def test_exact_button_assignment_factory_changes(
    final_apply_writes, factory_bytes
):
    memory = reconstruct_written_memory(final_apply_writes)
    assert bytes(factory_bytes[a] for a in range(0x009A, 0x009E)) == (
        bytes.fromhex("90 00 1e 00")
    )
    assert {
        address: (factory_bytes[address], memory.values[address])
        for address in range(0x009A, 0x009E)
        if factory_bytes[address] != memory.values[address]
    } == {
        0x009A: (0x90, 0x91),
        0x009C: (0x1E, 0x01),
    }


def test_final_apply_differs_from_brightness_only_for_assignment(
    project_root: Path, final_apply_writes
):
    path = (
        project_root / "usb-captures" / "analysis"
        / "brightness_medium_to_high" / "written_memory_map.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    brightness = {int(key, 16): value for key, value in document["bytes"].items()}
    memory = reconstruct_written_memory(final_apply_writes)
    assert set(memory.values) == set(brightness)
    assert {
        address: (brightness[address], value)
        for address, value in memory.values.items()
        if brightness[address] != value
    } == {
        0x009A: (0x90, 0x91),
        0x009C: (0x1E, 0x01),
    }


def test_initial_read_retains_prior_brightness_change(
    macro_capture, factory_bytes
):
    _, reads, _ = macro_capture
    memory = reconstruct_memory(reads)
    assert {
        address: (factory_bytes[address], value)
        for address, value in memory.values.items()
        if factory_bytes[address] != value
    } == {0x044F: (0x02, 0x03)}


def test_final_command_sequence_has_no_new_macro_selector(macro_capture):
    transfers, _, _ = macro_capture
    by_frame = {
        item.submission_frame: item
        for item in transfers if item.direction == "SET_REPORT"
    }
    expected = {
        4672: "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00",
        4674: "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00",
        4676: "02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00",
        4678: "02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00",
        4680: "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00",
    }
    assert {
        frame: by_frame[frame].payload.hex(" ") for frame in expected
    } == expected


def test_no_readback_after_macro_upload_or_apply(macro_capture):
    transfers, _, _ = macro_capture
    assert not [
        item for item in transfers
        if 4260 < item.submission_frame < 4370
        and item.direction == "GET_REPORT"
    ]
    assert not [
        item for item in transfers
        if item.submission_frame > 4680 and item.direction == "GET_REPORT"
    ]


def test_offline_parser_contains_no_live_device_path(project_root: Path):
    source = (
        REPO / "src" / "venus_profile"
        / "offline_usb_capture.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "/dev/hidraw", "hidd_setfeature", "import hid", "pyusb", "usb.core",
        "os.open(",
    ):
        assert forbidden not in source
