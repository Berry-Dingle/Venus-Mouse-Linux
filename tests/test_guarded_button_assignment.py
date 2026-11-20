from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.button_assignment_write import (
    ASSIGNMENT_F1_REPORT,
    build_assignment_write_plan,
    confirmed_assignment_from_id,
    confirmed_assignment_id,
    confirmed_assignment_raw,
    confirmed_assignments,
    serialize_assignment_f3,
)
from venus_profile.guarded_button_assignment import (
    GuardedButtonAssignmentError,
    _GuardedAssignmentTransport,
    _parser,
    execute_assignment_write,
    execute_restore,
    main,
    safety_audit,
)
from venus_profile.readonly_hidraw import HidrawDevice, HidrawPermissionError


DISABLE = bytes.fromhex("00 00 00 00")
FORWARD = bytes.fromhex("85 00 00 00")
SINGLE_DEFAULT = bytes.fromhex("90 00 1E 00")
SINGLE_CAMPAIGN = bytes.fromhex("90 00 04 00")
UNKNOWN = bytes.fromhex("90 00 1F 00")


def _device(**overrides) -> HidrawDevice:
    values = {
        "path": Path("/dev/mock-hidraw"),
        "sysfs_path": Path("/sys/mock"),
        "vendor_id": 0x04D9,
        "product_id": 0xFC55,
        "interface_number": 2,
        "usage_page": 0xFFA0,
        "usage": 1,
    }
    values.update(overrides)
    return HidrawDevice(**values)


class MockTransport:
    def __init__(self, current, *, f3_state=None, fail=None):
        self.current = current
        self.f3_state = f3_state
        self.fail = fail
        self.reports_sent: list[tuple[str, bytes]] = []
        self.closed = False

    def _read_plan_target(self, plan):
        if self.fail == "read":
            raise RuntimeError("mock read failure")
        return self.current

    def _send_validated_plan(self, plan):
        if self.fail == "f3":
            raise RuntimeError("mock F3 failure")
        report = serialize_assignment_f3(plan.profile, plan.button, plan.target)
        self.reports_sent.append(("assignment-f3", report))
        if self.f3_state is not None:
            self.current = self.f3_state
        return report

    def _sleeper(self, seconds):
        pass

    def close(self):
        self.closed = True


def _target(identifier: str):
    return confirmed_assignment_from_id(identifier)


def _write(tmp_path, *, source=FORWARD, target_id="record-00000000", answers=None, transport=None):
    target = _target(target_id)
    transport = transport or MockTransport(source, f3_state=target.raw)
    answers = iter(answers or ["CONTINUE"])
    result = execute_assignment_write(
        1, 7, target, output_root=tmp_path,
        input_fn=lambda prompt: next(answers),
        transport_factory=lambda device: transport,
        discovery=lambda: _device(), permission_check=lambda path: True,
    )
    return result, transport


def test_write_sends_exactly_one_f3_after_confirmation(tmp_path):
    result, transport = _write(tmp_path)
    assert [name for name, _ in transport.reports_sent] == ["assignment-f3"]
    assert result.final_record == DISABLE
    assert transport.reports_sent[0][1] == bytes.fromhex(
        "02 F3 9A 00 04 00 00 00 00 00 00 00 00 00 00 00"
    )


def test_write_summary_and_evidence_contain_exact_ids_and_raw_bytes(tmp_path):
    summaries = []
    transport = MockTransport(FORWARD, f3_state=DISABLE)
    result = execute_assignment_write(
        1, 7, _target("record-00000000"), output_root=tmp_path,
        input_fn=lambda prompt: "CONTINUE",
        output_fn=summaries.append,
        transport_factory=lambda device: transport,
        discovery=lambda: _device(), permission_check=lambda path: True,
    )
    assert "record-85000000 85 00 00 00" in summaries[0]
    assert "record-00000000 00 00 00 00" in summaries[0]
    document = json.loads((result.output / "operation.json").read_text())
    assert document["source_id"] == "record-85000000"
    assert document["target_id"] == "record-00000000"
    assert document["f1_sent"] is False
    assert document["f5_sent"] is False
    assert document["automatic_retry"] is False


@pytest.mark.parametrize("answer", ["continue", "Continue", " CONTINUE", "CONTINUE ", "", "yes"])
def test_write_requires_exact_continue_before_any_report(tmp_path, answer):
    transport = MockTransport(FORWARD)
    with pytest.raises(GuardedButtonAssignmentError, match="confirmation"):
        _write(tmp_path, answers=[answer], transport=transport)
    assert transport.reports_sent == []


def test_write_unknown_source_and_equal_target_are_rejected(tmp_path):
    with pytest.raises(GuardedButtonAssignmentError, match="exact confirmed"):
        _write(tmp_path, source=UNKNOWN)
    transport = MockTransport(DISABLE)
    with pytest.raises(GuardedButtonAssignmentError, match="differ"):
        _write(tmp_path, source=DISABLE, transport=transport)
    assert transport.reports_sent == []


def test_write_target_readback_mismatch_fails_closed(tmp_path):
    transport = MockTransport(FORWARD, f3_state=FORWARD)
    with pytest.raises(GuardedButtonAssignmentError, match="mismatch"):
        _write(tmp_path, transport=transport)
    assert len(transport.reports_sent) == 1


def test_write_failures_do_not_retry_or_emit_forbidden_reports(tmp_path):
    for failure in ("f3", "read"):
        transport = MockTransport(FORWARD, fail=failure)
        with pytest.raises(GuardedButtonAssignmentError):
            _write(tmp_path / failure, transport=transport)
        assert len(transport.reports_sent) <= 1
        assert all(len(report) == 16 for _, report in transport.reports_sent)
        assert all(report[1] == 0xF3 for _, report in transport.reports_sent)


@pytest.mark.parametrize("original", [SINGLE_DEFAULT, SINGLE_CAMPAIGN])
def test_restore_preserves_duplicate_label_records_exactly(tmp_path, original):
    evidence, _ = _write(tmp_path / "write", source=original)
    transport = MockTransport(DISABLE, f3_state=original)
    result = execute_restore(
        evidence.output, output_root=tmp_path / "restore",
        input_fn=lambda prompt: "CONTINUE",
        transport_factory=lambda device: transport,
        discovery=lambda: _device(), permission_check=lambda path: True,
    )
    assert result.final_record == original
    assert transport.reports_sent[0][1][8:12] == original


def test_restore_accepts_historical_label_only_with_exact_raw_record(tmp_path):
    evidence, _ = _write(tmp_path / "write", source=SINGLE_DEFAULT)
    operation_path = evidence.output / "operation.json"
    operation = json.loads(operation_path.read_text())
    assert operation["source_label"] == "Single Key 1"
    # Evidence recorded before the label correction used "Single Key A".
    operation["source_label"] = "Single Key A"
    operation_path.write_text(json.dumps(operation))
    transport = MockTransport(DISABLE, f3_state=SINGLE_DEFAULT)
    result = execute_restore(
        evidence.output, output_root=tmp_path / "restore",
        input_fn=lambda prompt: "CONTINUE",
        transport_factory=lambda device: transport,
        discovery=lambda: _device(), permission_check=lambda path: True,
    )
    assert result.final_record == SINGLE_DEFAULT
    # Any other wrong label is still rejected before transport.
    operation["source_label"] = "Single Key B"
    operation_path.write_text(json.dumps(operation))
    with pytest.raises(GuardedButtonAssignmentError, match="raw/ID"):
        execute_restore(evidence.output, transport_factory=lambda device: pytest.fail())


def test_restore_requires_saved_target_current_and_rejects_edited_evidence(tmp_path):
    evidence, _ = _write(tmp_path / "write", target_id="record-00000000")
    wrong_current = MockTransport(FORWARD)
    with pytest.raises(GuardedButtonAssignmentError, match="saved target"):
        execute_restore(
            evidence.output, transport_factory=lambda device: wrong_current,
            discovery=lambda: _device(), permission_check=lambda path: True,
        )
    operation_path = evidence.output / "operation.json"
    operation = json.loads(operation_path.read_text())
    operation["target_id"] = "record-85000000"
    operation_path.write_text(json.dumps(operation))
    with pytest.raises(GuardedButtonAssignmentError, match="raw/ID"):
        execute_restore(evidence.output, transport_factory=lambda device: pytest.fail())


def test_restore_rejects_noncanonical_or_malformed_evidence(tmp_path):
    evidence, _ = _write(tmp_path / "write")
    operation_path = evidence.output / "operation.json"
    operation = json.loads(operation_path.read_text())
    operation["source"] = "85 00 00"
    operation_path.write_text(json.dumps(operation))
    with pytest.raises(GuardedButtonAssignmentError):
        execute_restore(evidence.output, transport_factory=lambda device: pytest.fail())


def test_exact_scope_addresses_and_offline_cli():
    for profile in range(1, 6):
        for button in range(1, 21):
            assert 0 <= profile <= 5 and 0 < button <= 20
    called = []
    assert main([], discovery=lambda: called.append(True)) == 0
    assert called == []
    with pytest.raises(SystemExit) as error:
        _parser().parse_args(["--live-button-assignment-experiment"])
    assert error.value.code == 2


def test_identity_and_permission_fail_before_transport():
    with pytest.raises(GuardedButtonAssignmentError, match="exact 04D9"):
        execute_assignment_write(
            1, 7, _target("record-00000000"), discovery=lambda: _device(interface_number=1),
            transport_factory=lambda device: pytest.fail(),
        )
    with pytest.raises(HidrawPermissionError):
        main(
            ["--write-assignment", "--profile", "1", "--button", "7",
             "--assignment-id", "record-00000000", "--acknowledge-device-write"],
            discovery=lambda: _device(), permission_check=lambda path: False,
            isatty_fn=lambda: True,
        )


def test_static_audit_and_no_generic_transport_surface():
    assert len(safety_audit()) == 5
    assert not hasattr(_GuardedAssignmentTransport, "send_report")
    assert not hasattr(_GuardedAssignmentTransport, "write_memory")
    assert not hasattr(_GuardedAssignmentTransport, "send_f1")
