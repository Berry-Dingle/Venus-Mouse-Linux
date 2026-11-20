from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.button_assignments import (
    BUTTON_OFFSETS,
    CONFIRMED_ACTION_LABELS,
    PROFILE_BASES,
    ButtonAssignmentRecord,
    button_assignment_address,
    decode_profile_assignment_region,
    decode_saved_assignments,
)
from venus_profile.pfd import PfdProfile


FACTORY_RECORDS = [
    "81 00 00 00", "82 00 00 00", "83 00 00 00", "99 81 03 00",
    "8A 00 00 00", "89 00 00 00", "90 00 1E 00", "90 00 1F 00",
    "90 00 20 00", "90 00 21 00", "90 00 22 00", "90 00 23 00",
    "90 00 24 00", "90 00 25 00", "90 00 26 00", "90 00 27 00",
    "90 00 57 00", "90 00 56 00", "8B 00 00 00", "8C 00 00 00",
]


def assignment_memory(path: Path) -> dict[int, int]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {int(address, 16): value for address, value in document["bytes"].items()}


def record_bytes(memory: dict[int, int], profile: int, button: int) -> bytes:
    address = button_assignment_address(profile, button)
    return bytes(memory[address + offset] for offset in range(4))


def test_all_profile_bases_and_spacing():
    assert tuple(PROFILE_BASES) == (1, 2, 3, 4, 5)
    assert [PROFILE_BASES[index] for index in range(1, 6)] == [
        0x0082, 0x0142, 0x01F2, 0x02A2, 0x0352,
    ]
    # The first two captured groups are 0xC0 apart; the remaining saved
    # evidence uses 0xB0 spacing. Do not normalize these into one stride.
    assert [PROFILE_BASES[index + 1] - PROFILE_BASES[index] for index in range(1, 5)] == [
        0xC0, 0xB0, 0xB0, 0xB0,
    ]


def test_all_button_offsets_include_trailing_records():
    assert len(BUTTON_OFFSETS) == 20
    assert BUTTON_OFFSETS[:18] == tuple(range(0, 0x48, 4))
    assert BUTTON_OFFSETS[-2:] == (0x58, 0x5C)
    assert [button_assignment_address(1, button) for button in range(1, 21)] == [
        0x0082, 0x0086, 0x008A, 0x008E, 0x0092, 0x0096,
        0x009A, 0x009E, 0x00A2, 0x00A6, 0x00AA, 0x00AE,
        0x00B2, 0x00B6, 0x00BA, 0x00BE, 0x00C2, 0x00C6,
        0x00DA, 0x00DE,
    ]


def test_profile_1_button_7_address():
    assert button_assignment_address(1, 7) == 0x009A


def test_factory_default_assignment_map(project_root):
    memory = assignment_memory(
        project_root / "usb-captures" / "analysis" / "factory_memory_map.json"
    )
    for profile in range(1, 6):
        assert [record_bytes(memory, profile, button).hex(" ").upper()
                for button in range(1, 21)] == FACTORY_RECORDS


def test_controlled_macro_transition_and_saved_readback(project_root):
    factory = assignment_memory(
        project_root / "usb-captures" / "analysis" / "factory_memory_map.json"
    )
    saved = assignment_memory(
        project_root / "usb-captures" / "live-reads"
        / "venus_read_20260727T154117.272912Z" / "live_memory_map.json"
    )
    assert record_bytes(factory, 1, 7) == bytes.fromhex("90 00 1E 00")
    assert record_bytes(saved, 1, 7) == bytes.fromhex("91 00 01 00")


def test_decode_confirmed_macro_semantics():
    record = ButtonAssignmentRecord(1, 7, 0x009A, bytes.fromhex("91 00 01 00"))
    assert record.function_code == 0x91
    assert record.macro_assignment is not None
    assert record.macro_assignment.slot_index == 0
    assert record.macro_assignment.while_held is False
    assert record.macro_assignment.toggle is False
    assert record.macro_assignment.execution_count == 1


def test_unknown_function_code_has_no_invented_semantics():
    raw = bytes.fromhex("90 00 1F 00")
    record = ButtonAssignmentRecord(1, 7, 0x009A, raw)
    assert record.raw == raw
    assert record.function_code == 0x90
    assert record.macro_assignment is None
    assert record.confirmed_action is None


@pytest.mark.parametrize(
    ("raw", "label"),
    [(raw, label) for raw, label in CONFIRMED_ACTION_LABELS.items()],
)
def test_confirmed_complete_nonmacro_records_have_only_evidence_labels(raw, label):
    record = ButtonAssignmentRecord(1, 7, 0x009A, raw)
    assert record.raw == raw
    assert record.function_code == raw[0]
    assert record.confirmed_action == label
    assert record.macro_assignment is None


def test_0x90_is_not_generalized_beyond_confirmed_a_record():
    record = ButtonAssignmentRecord(1, 7, 0x009A, bytes.fromhex("90 00 1F 00"))
    assert record.confirmed_action is None


@pytest.mark.parametrize(
    "bad",
    [
        "8F 0A 00 01",
        "8F 02 05 00",
        "8F 00 00 00",
        "97 00 00 01",
        "98 01 00 00",
    ],
)
def test_near_match_or_arbitrary_0x8f_records_remain_unknown(bad):
    record = ButtonAssignmentRecord(1, 7, 0x009A, bytes.fromhex(bad))
    assert record.raw == bytes.fromhex(bad)
    assert record.confirmed_action is None


def test_confirmed_fire_key_records_have_exact_labels():
    record = ButtonAssignmentRecord(
        1, 4, 0x008E, bytes.fromhex("99 81 03 00")
    )
    assert record.confirmed_action == "Fire Key: Left, 3 fires, 0 ms"


def test_fire_campaign_readbacks_and_repeated_reset(project_root):
    readback_names = [
        "venus_read_20260728T112815.539927Z",
        "venus_read_20260728T114007.180464Z",
        "venus_read_20260728T114248.543525Z",
        "venus_read_20260728T114533.313004Z",
        "venus_read_20260728T114708.706923Z",
        "venus_read_20260728T114846.341422Z",
    ]
    expected = [
        bytes.fromhex("99 81 03 00"),
        bytes.fromhex("99 81 04 01"),
        bytes.fromhex("99 81 03 02"),
        bytes.fromhex("99 82 03 01"),
        bytes.fromhex("99 84 03 01"),
        bytes.fromhex("99 04 03 01"),
    ]
    memories = [
        assignment_memory(
            project_root / "usb-captures" / "live-reads" / name
            / "live_memory_map.json"
        )
        for name in readback_names
    ]
    repeated_reset = assignment_memory(
        project_root / "usb-captures" / "live-reads"
        / "venus_read_20260728T120725.592862Z" / "live_memory_map.json"
    )
    assert memories[0] == repeated_reset
    assert [record_bytes(memory, 1, 4) for memory in memories] == expected
    assert [
        ButtonAssignmentRecord(1, 4, 0x008E, raw).confirmed_action
        for raw in expected
    ] == [
        "Fire Key: Left, 3 fires, 0 ms",
        "Fire Key: Left, 4 fires, 10 ms",
        "Fire Key: Left, 3 fires, 20 ms",
        "Fire Key: Right, 3 fires, 10 ms",
        "Fire Key: Middle, 3 fires, 10 ms",
        "Fire Key: Keyboard A, 3 fires, 10 ms",
    ]
    baseline = memories[0]
    expected_changed_addresses = [
        [0x0090, 0x0091],
        [0x0091],
        [0x008F, 0x0091],
        [0x008F, 0x0091],
        [0x008F, 0x0091],
    ]
    for memory, expected_addresses in zip(memories[1:], expected_changed_addresses):
        changed = [
            address for address in sorted(set(baseline) | set(memory))
            if baseline.get(address) != memory.get(address)
        ]
        assert changed == expected_addresses


@pytest.mark.parametrize(
    "raw",
    [
        "99 81 03 03",
        "99 81 05 01",
        "99 80 03 01",
        "99 04 04 01",
        "99 81 03 FF",
    ],
)
def test_arbitrary_or_unsupported_fire_records_remain_unknown(raw):
    record = ButtonAssignmentRecord(1, 4, 0x008E, bytes.fromhex(raw))
    assert record.raw == bytes.fromhex(raw)
    assert record.function_code == 0x99
    assert record.confirmed_action is None


def test_combo_and_report_rate_campaign_readbacks(project_root):
    readback_names = [
        "venus_read_20260728T103818.897397Z",
        "venus_read_20260728T103951.112958Z",
        "venus_read_20260728T104145.101013Z",
        "venus_read_20260728T104323.829642Z",
    ]
    expected = [
        bytes.fromhex("8F 0A 00 00"),
        bytes.fromhex("8F 02 04 00"),
        bytes.fromhex("97 00 00 00"),
        bytes.fromhex("98 00 00 00"),
    ]
    memories = [
        assignment_memory(
            project_root / "usb-captures" / "live-reads" / name
            / "live_memory_map.json"
        )
        for name in readback_names
    ]
    assert [record_bytes(memory, 1, 7) for memory in memories] == expected
    for before, after in zip(memories, memories[1:]):
        changed_records = [
            (profile, button)
            for profile in range(1, 6)
            for button in range(1, 21)
            if record_bytes(before, profile, button)
            != record_bytes(after, profile, button)
        ]
        assert changed_records == [(1, 7)]


def test_region_and_saved_decode_preserve_records(project_root):
    memory = assignment_memory(
        project_root / "usb-captures" / "analysis" / "factory_memory_map.json"
    )
    regions = decode_saved_assignments(memory)
    assert [region.profile for region in regions] == [1, 2, 3, 4, 5]
    for region in regions:
        assert len(region.records) == 20
        for record in region.records:
            assert record.raw == record_bytes(memory, record.profile, record.button)

    contiguous = bytearray(0x60)
    for button, offset in enumerate(BUTTON_OFFSETS, start=1):
        contiguous[offset:offset + 4] = bytes.fromhex(FACTORY_RECORDS[button - 1])
    region = decode_profile_assignment_region(1, bytes(contiguous))
    assert region.records[6].raw == bytes.fromhex("90 00 1E 00")
    assert region.raw_region == bytes(contiguous)


def test_relevant_pfd_button_samples_remain_unmodified(controlled):
    profile = PfdProfile.from_path(controlled / "00_Baseline_NoMacro.pfd")
    assert [button.function for button in profile.buttons] == [
        1, 2, 3, 12, 48, 49, *([6] * 12), 52, 53,
    ]
    assert profile.to_bytes() == (controlled / "00_Baseline_NoMacro.pfd").read_bytes()


def test_decoder_module_has_no_live_device_surface():
    source = Path(__file__).parents[1] / "src" / "venus_profile" / "button_assignments.py"
    text = source.read_text(encoding="utf-8")
    assert "/dev/hidraw" not in text
    assert "/dev/bus/usb" not in text
    assert "subprocess" not in text
    assert "hidraw" not in text.lower()
    assert "discover" not in text.lower()


@pytest.mark.parametrize("bad", [b"", b"\x00" * 3, b"\x00" * 5])
def test_malformed_record_length_rejected(bad):
    with pytest.raises(ValueError, match="exactly four bytes"):
        ButtonAssignmentRecord(1, 1, 0x0082, bad)


def test_invalid_address_inputs_rejected():
    with pytest.raises(ValueError):
        button_assignment_address(0, 1)
    with pytest.raises(ValueError):
        button_assignment_address(1, 21)
