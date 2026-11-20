from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path

import pytest

from venus_profile.button_assignment_write import (
    ASSIGNMENT_F1_REPORT,
    LIVE_EXECUTION_STATUS,
    REPORT2_LENGTH,
    AssignmentWritePlan,
    ConfirmedAssignment,
    build_assignment_write_plan,
    confirmed_assignment,
    confirmed_assignment_from_id,
    confirmed_assignment_id,
    confirmed_assignment_raw,
    confirmed_assignments,
    safety_audit,
    serialize_assignment_f3,
)
from venus_profile.button_assignments import (
    BUTTON_OFFSETS,
    CONFIRMED_ACTION_LABELS,
    PROFILE_BASES,
    button_assignment_address,
)


def test_profile_1_button_7_exact_f3_bytes():
    target = confirmed_assignment("Forward")
    report = serialize_assignment_f3(1, 7, target)
    assert report == bytes.fromhex(
        "02 F3 9A 00 04 00 00 00 85 00 00 00 00 00 00 00"
    )


def test_all_profile_button_addresses_are_calculated_from_decoder_logic():
    for profile, base in PROFILE_BASES.items():
        for button, offset in enumerate(BUTTON_OFFSETS, start=1):
            assert button_assignment_address(profile, button) == base + offset
            report = serialize_assignment_f3(
                profile, button, confirmed_assignment("Disable")
            )
            assert int.from_bytes(report[2:4], "little") == base + offset


def test_every_decoder_confirmed_label_is_accepted_without_second_mapping():
    assignments = confirmed_assignments()
    assert {item.raw: item.label for item in assignments} == CONFIRMED_ACTION_LABELS
    for item in assignments:
        assert ConfirmedAssignment(item.label, item.raw) == item


def test_unknown_and_arbitrary_records_are_rejected():
    with pytest.raises(ValueError, match="exactly four bytes"):
        ConfirmedAssignment("unknown", b"\x99\x81\x03")
    with pytest.raises(ValueError, match="exact confirmed record"):
        ConfirmedAssignment("invented", bytes.fromhex("99 81 03 03"))
    with pytest.raises(ValueError, match="exact confirmed record"):
        ConfirmedAssignment("Fire Key: Left, 3 fires, 0 ms", bytes.fromhex("99 81 03 03"))


def test_exact_raw_lookup_distinguishes_duplicate_single_key_labels():
    factory = confirmed_assignment_raw(bytes.fromhex("90 00 1E 00"))
    campaign = confirmed_assignment_raw(bytes.fromhex("90 00 04 00"))
    # Usage 0x1E is the "1" key; the historical "Single Key A" label for the
    # factory record was corrected (see button_assignments.py).
    assert factory.label == "Single Key 1"
    assert campaign.label == "Single Key A"
    assert factory.raw == bytes.fromhex("90 00 1E 00")
    assert campaign.raw == bytes.fromhex("90 00 04 00")
    assert factory != campaign


def test_stable_ids_cover_every_confirmed_assignment_and_duplicate_records():
    assignments = confirmed_assignments()
    ids = [confirmed_assignment_id(item) for item in assignments]
    assert len(ids) == len(set(ids))
    for item, identifier in zip(assignments, ids):
        assert identifier.startswith("record-")
        assert len(identifier) == len("record-") + 8
        resolved = confirmed_assignment_from_id(identifier)
        assert resolved.raw == item.raw
        assert resolved.label == item.label
    assert confirmed_assignment_id(
        confirmed_assignment_raw(bytes.fromhex("90 00 04 00"))
    ) != confirmed_assignment_id(
        confirmed_assignment_raw(bytes.fromhex("90 00 1E 00"))
    )


@pytest.mark.parametrize("identifier", [
    "record-9000040",
    "record-900004000",
    "record-9000040G",
    "record-9000040a",
    "Record-90000400",
    "record_90000400",
    "raw-90000400",
    "record-90001F00",
])
def test_stable_ids_reject_noncanonical_or_unknown_values(identifier):
    with pytest.raises(ValueError):
        confirmed_assignment_from_id(identifier)


@pytest.mark.parametrize("raw", [b"\x90\x00\x1E", b"\x90\x00\x1E\x00\x00"])
def test_exact_raw_lookup_rejects_malformed_lengths(raw):
    with pytest.raises(ValueError, match="exactly four bytes"):
        confirmed_assignment_raw(raw)


def test_exact_raw_lookup_rejects_unknown_record():
    with pytest.raises(ValueError, match="exact confirmed record"):
        confirmed_assignment_raw(bytes.fromhex("90 00 1F 00"))


def test_exact_f1_report_is_fixed():
    assert ASSIGNMENT_F1_REPORT == bytes.fromhex(
        "02 F1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert len(ASSIGNMENT_F1_REPORT) == REPORT2_LENGTH


def test_plan_contains_one_target_and_preserves_raw_records():
    source = confirmed_assignment("Disable")
    target = confirmed_assignment("Forward")
    plan = build_assignment_write_plan(1, 7, source, target)
    assert plan.profile == 1
    assert plan.button == 7
    assert plan.address == 0x009A
    assert plan.source.raw == bytes.fromhex("00 00 00 00")
    assert plan.target.raw == bytes.fromhex("85 00 00 00")
    assert plan.f3_report[8:12] == plan.target.raw
    assert plan.f1_report == ASSIGNMENT_F1_REPORT
    assert plan.evidence_status == LIVE_EXECUTION_STATUS


def test_plan_is_immutable_and_has_no_multi_target_fields():
    plan = build_assignment_write_plan(
        1, 7, confirmed_assignment("Disable"), confirmed_assignment("Forward")
    )
    with pytest.raises(FrozenInstanceError):
        plan.target = confirmed_assignment("Single Key A")
    assert not any(
        name in inspect.signature(build_assignment_write_plan).parameters
        for name in ("targets", "profiles", "buttons", "address", "payload")
    )


def test_source_equals_target_and_invalid_scope_are_rejected():
    disable = confirmed_assignment("Disable")
    with pytest.raises(ValueError, match="must differ"):
        build_assignment_write_plan(1, 7, disable, disable)
    with pytest.raises(ValueError):
        build_assignment_write_plan(0, 7, disable, confirmed_assignment("Forward"))
    with pytest.raises(ValueError):
        build_assignment_write_plan(1, 21, disable, confirmed_assignment("Forward"))


def test_reports_have_fixed_reserved_and_trailing_bytes():
    report = serialize_assignment_f3(5, 20, confirmed_assignment("Disable"))
    assert len(report) == 16
    assert report[:2] == bytes.fromhex("02 F3")
    assert report[4:8] == bytes.fromhex("04 00 00 00")
    assert report[12:] == bytes.fromhex("00 00 00 00")


def test_module_has_no_live_device_surface():
    source_path = Path(__file__).parents[1] / "src" / "venus_profile" / "button_assignment_write.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imports.isdisjoint({"hid", "hidraw", "usb", "fcntl", "subprocess"})
    functions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert functions.isdisjoint({"send", "send_report", "write_report", "discover", "execute", "main"})


def test_new_static_safety_audit_passes():
    checks = safety_audit()
    assert len(checks) == 5
