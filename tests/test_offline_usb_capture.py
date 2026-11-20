from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path

import pytest

from venus_profile.offline_usb_capture import (
    analyse_capture,
    reconstruct_memory,
)


@pytest.fixture(scope="module")
def factory_analysis(project_root: Path):
    capture = (
        project_root / "usb-captures"
        / "factory_first_open_20260727_074015.pcapng"
    )
    return analyse_capture(capture)


def test_capture_transaction_counts(factory_analysis):
    transfers, reads, memory = factory_analysis
    assert len(transfers) == 388
    assert Counter(item.direction for item in transfers) == {
        "SET_REPORT": 195,
        "GET_REPORT": 193,
    }
    assert len(reads) == 193
    assert len(memory.values) == 4604


def test_known_f5_frames(factory_analysis):
    transfers, _, _ = factory_analysis
    by_submission = {item.submission_frame: item for item in transfers}
    assert by_submission[24100].completion_frame == 24101
    assert by_submission[24100].payload == bytes.fromhex(
        "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert by_submission[24876].completion_frame == 24877
    assert by_submission[24876].payload == bytes.fromhex(
        "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )


def test_known_first_f2_request(factory_analysis):
    transfers, reads, _ = factory_analysis
    by_submission = {item.submission_frame: item for item in transfers}
    request = by_submission[24102]
    assert request.completion_frame == 24103
    assert request.report_id == 2
    assert request.report_type == 3
    assert request.interface_index == 2
    assert request.requested_report_length == 16
    assert request.payload == bytes.fromhex(
        "02 f2 2c 00 01 00 00 00 00 00 00 00 00 00 00 00"
    )
    read = reads[0]
    assert read.address == 0x002C
    assert read.requested_length == 1


def test_known_first_get_response_bytes(factory_analysis):
    transfers, reads, _ = factory_analysis
    by_submission = {item.submission_frame: item for item in transfers}
    get_report = by_submission[24104]
    assert get_report.completion_frame == 24105
    assert get_report.payload == bytes.fromhex("02 08 2c 60 01 00 fa fa 00")
    assert reads[0].returned_data == b"\x00"


def test_known_second_request_and_get_pair(factory_analysis):
    _, reads, _ = factory_analysis
    read = next(
        item for item in reads if item.set_transfer.submission_frame == 24106
    )
    assert read.set_transfer.completion_frame == 24107
    assert read.get_transfer.submission_frame == 24108
    assert read.get_transfer.completion_frame == 24109
    assert read.address == 0x0449
    assert read.requested_length == 7
    assert read.response == bytes.fromhex(
        "02 08 48 64 07 00 fa fa ff 00 00 02 05 01 02"
    )
    assert read.transformed_address == 0x0448
    assert read.returned_data == bytes.fromhex("ff 00 00 02 05 01 02")


def test_all_f2_responses_validate_and_pair_in_sequence(factory_analysis):
    _, reads, _ = factory_analysis
    assert all(not read.validate_response() for read in reads)
    assert all(
        read.set_transfer.completion_frame
        < read.get_transfer.submission_frame
        < read.get_transfer.completion_frame
        for read in reads
    )
    assert len({
        (read.address, read.requested_length) for read in reads
    }) == len(reads)


def test_report_selection_and_requested_lengths(factory_analysis):
    _, reads, _ = factory_analysis
    assert Counter(
        (
            read.set_transfer.report_id,
            read.set_transfer.requested_report_length,
            read.requested_length,
        )
        for read in reads
    ) == {
        (2, 16, 1): 1,
        (2, 16, 3): 1,
        (2, 16, 4): 100,
        (2, 16, 5): 1,
        (2, 16, 7): 5,
        (3, 64, 32): 5,
        (3, 64, 50): 80,
    }


def test_memory_reconstruction_ranges_gaps_and_overlaps(factory_analysis):
    _, reads, memory = factory_analysis
    assert memory.minimum_address == 0x002C
    assert memory.maximum_address == 0x1417
    assert len(memory.ranges()) == 24
    assert len(memory.gaps()) == 23
    assert sum(end - start + 1 for start, end in memory.gaps()) == 496
    assert memory.overlaps == []
    assert memory.ranges()[-1] == (0x0478, 0x1417)
    assert len(memory.flat_bytes()) == 5100
    assert reconstruct_memory(reads).values == memory.values


def test_memory_reconstruction_is_deterministic(factory_analysis):
    _, reads, memory = factory_analysis
    reverse_order = reconstruct_memory(reversed(reads))
    assert reverse_order.values == memory.values
    assert reverse_order.overlaps == []
    assert hashlib.sha256(memory.flat_bytes()).hexdigest() == (
        "fa304c2aaa332e2b18491c11ed4a2d6527948114fa75a8aeb271b0ed09abf1a8"
    )


def test_all_five_profiles_and_twenty_device_macro_slots_are_read(
    factory_analysis,
):
    _, reads, memory = factory_analysis
    addresses = {(item.address, item.requested_length) for item in reads}
    assert {
        (0x0042, 32), (0x0102, 32), (0x01B2, 32),
        (0x0262, 32), (0x0312, 32),
    } <= addresses
    assert {
        (0x0449, 7), (0x0451, 7), (0x0459, 7),
        (0x0461, 7), (0x0469, 7),
    } <= addresses
    assert all(address in memory.values for address in range(0x0478, 0x1418))
    assert (0x1418 - 0x0478) // 0x00C8 == 20
