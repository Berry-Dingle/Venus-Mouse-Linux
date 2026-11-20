from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from venus_profile.guarded_brightness import (
    BrightnessValue,
    GuardedBrightnessError,
    execute_medium_to_high,
)
from venus_profile.known_device_state import (
    KNOWN_STATE_PATH,
    KnownDeviceStateError,
    atomic_write_state,
    compare_live_read,
    confirm_pending_persistence,
    effective_expected_state,
    empty_state,
    load_known_state,
    main,
    record_confirmed_transition,
    validate_state_document,
    windows_captured_baseline,
)
from venus_profile.readonly_hidraw import HidrawDevice, validate_f2_request
from evidence_paths import EVIDENCE  # noqa: E402


POST_CYCLE_READ = (
    EVIDENCE / "usb-captures" / "live-reads"
    / "venus_read_20260727T090950.407810Z"
)


def _record_pending(path: Path, *, timestamp: str = "2026-07-27T09:02:36+00:00"):
    return record_confirmed_transition(
        state_path=path,
        address=0x044F,
        previous_value=0x03,
        confirmed_new_value=0x02,
        operation_timestamp=timestamp,
        result_directory=path.parent / "write-result",
        updated_at=timestamp,
    )


def _write_confirmed_medium_state(path: Path):
    state = _record_pending(path)
    state["history"][0]["power_cycle_persistence"] = "confirmed"
    state["history"][0]["persistence_confirmed_at"] = (
        "2026-07-27T09:10:00+00:00"
    )
    state["history"][0]["latest_confirming_live_read_directory"] = str(
        POST_CYCLE_READ
    )
    atomic_write_state(path, state)
    return state


def _copy_live_evidence(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    for name in ("live_transactions.json", "live_memory_map.json"):
        shutil.copyfile(source / name, destination / name)
    return destination


def _device() -> HidrawDevice:
    return HidrawDevice(
        path=Path("/mock/hidraw4"),
        sysfs_path=Path("/mock/sysfs"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=2,
        usage_page=0xFFA0,
        usage=1,
    )


def _response(value: int) -> bytes:
    return bytes.fromhex("02 08 4e 64 01 00 fa fa") + bytes([value])


class RestorationBackend:
    def __init__(self, values: list[int]):
        self.responses = [_response(value) for value in values]
        self.writes: list[bytes] = []
        self.closed = False

    def send_f2(self, request: bytes) -> None:
        validate_f2_request(request)

    def get_feature(self, report_id: int, length: int) -> bytes:
        return self.responses.pop(0)

    def send_brightness(self, value: BrightnessValue) -> bytes:
        from venus_profile.guarded_brightness import build_brightness_report
        report = build_brightness_report(value)
        self.writes.append(report)
        return report

    def finalise_lighting(self) -> bytes:
        from venus_profile.guarded_brightness import LIGHTING_FINALISATION_REPORT
        self.writes.append(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT

    def close(self) -> None:
        self.closed = True


def test_project_state_is_valid_and_latest_history_drives_overlay():
    state = load_known_state(KNOWN_STATE_PATH)
    assert len(state["history"]) >= 1
    effective = effective_expected_state()
    assert effective.values[0x044F] == state["history"][-1][
        "confirmed_new_value"
    ]
    assert effective.sources[0x044F] == "confirmed-linux-state-overlay"


def test_state_overlay_creation_is_atomic(tmp_path: Path):
    state_path = tmp_path / "state" / "known.json"
    _record_pending(state_path)
    state = load_known_state(state_path)
    assert state["current_overrides"] == {"0x044F": 2}
    assert state["history"][0]["power_cycle_persistence"] == "pending"
    assert list(state_path.parent.glob(".*.tmp")) == []


def test_history_is_preserved_across_restoration(tmp_path: Path):
    state_path = tmp_path / "known.json"
    first = _record_pending(state_path)
    first["history"][0]["power_cycle_persistence"] = "confirmed"
    first["history"][0]["persistence_confirmed_at"] = (
        "2026-07-27T09:10:00+00:00"
    )
    first["history"][0]["latest_confirming_live_read_directory"] = (
        str(tmp_path / "confirming-read")
    )
    atomic_write_state(state_path, first)
    record_confirmed_transition(
        state_path=state_path,
        address=0x044F,
        previous_value=0x02,
        confirmed_new_value=0x03,
        operation_timestamp="2026-07-27T10:00:00+00:00",
        result_directory=tmp_path / "restore",
        updated_at="2026-07-27T10:00:01+00:00",
    )
    state = load_known_state(state_path)
    assert len(state["history"]) == 2
    assert [entry["confirmed_new_value"] for entry in state["history"]] == [2, 3]
    assert state["current_overrides"] == {"0x044F": 3}


def test_new_transition_is_rejected_while_persistence_is_pending(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    with pytest.raises(KnownDeviceStateError, match="still pending"):
        record_confirmed_transition(
            state_path=state_path,
            address=0x044F,
            previous_value=0x02,
            confirmed_new_value=0x03,
            operation_timestamp="2026-07-27T10:00:00+00:00",
            result_directory=tmp_path / "restore",
            updated_at="2026-07-27T10:00:01+00:00",
        )


def test_guarded_executor_rejects_pending_state_before_transport_open(
    tmp_path: Path,
):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    opened = []
    with pytest.raises(GuardedBrightnessError, match="pending power-cycle"):
        execute_medium_to_high(
            _device(),
            output_root=tmp_path / "results",
            state_path=state_path,
            transport_factory=lambda device: opened.append(device),
        )
    assert opened == []


def test_malformed_state_is_never_ignored(tmp_path: Path):
    path = tmp_path / "known.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KnownDeviceStateError, match="could not parse"):
        load_known_state(path)
    with pytest.raises(KnownDeviceStateError):
        effective_expected_state(state_path=path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("vendor_id", "1234"), ("product_id", "0001"), ("interface_number", "01")],
)
def test_wrong_device_identity_is_rejected(tmp_path: Path, field: str, value: str):
    document = empty_state("2026-07-27T10:00:00+00:00")
    document["device"][field] = value
    with pytest.raises(KnownDeviceStateError, match="04d9:fc55"):
        validate_state_document(document)


def test_conflicting_override_is_rejected(tmp_path: Path):
    path = tmp_path / "known.json"
    state = _record_pending(path)
    state["current_overrides"]["0x044F"] = 3
    with pytest.raises(KnownDeviceStateError, match="conflict"):
        validate_state_document(state)


def test_conflicting_history_chain_is_rejected(tmp_path: Path):
    path = tmp_path / "known.json"
    state = _record_pending(path)
    state["history"][0]["previous_value"] = 2
    with pytest.raises(KnownDeviceStateError, match="conflicting history"):
        validate_state_document(state)


def test_post_cycle_read_compares_with_zero_unexpected_differences(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _write_confirmed_medium_state(state_path)
    comparison = compare_live_read(POST_CYCLE_READ, state_path=state_path)
    assert comparison["returned_byte_count"] == 4604
    assert comparison["unexpected_difference_count"] == 0
    assert comparison["windows_baseline_expectation_count"] == 4603
    assert comparison["linux_overlay_expectation_count"] == 1
    override = comparison["confirmed_linux_overrides_read"][0]
    assert override["address"] == 0x044F
    assert override["actual"] == 2
    assert override["matched"]


def test_windows_baseline_and_factory_artifact_remain_unchanged(
    project_root: Path, tmp_path: Path
):
    factory = (
        project_root / "usb-captures" / "analysis" / "factory_memory_map.json"
    )
    before = hashlib.sha256(factory.read_bytes()).hexdigest()
    baseline = windows_captured_baseline()
    assert baseline[0x044F] == 0x03
    state_path = tmp_path / "known.json"
    _write_confirmed_medium_state(state_path)
    effective = effective_expected_state(state_path=state_path)
    assert effective.values[0x044F] == 0x02
    assert hashlib.sha256(factory.read_bytes()).hexdigest() == before


def test_offline_persistence_confirmation_from_complete_live_read(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    confirmed = confirm_pending_persistence(
        POST_CYCLE_READ,
        state_path=state_path,
        clock=lambda: datetime(2026, 7, 27, 9, 10, tzinfo=timezone.utc),
    )
    transition = confirmed["history"][0]
    assert transition["power_cycle_persistence"] == "confirmed"
    assert transition["latest_confirming_live_read_directory"] == str(
        POST_CYCLE_READ
    )


def test_invalid_persistence_value_evidence_is_rejected(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    evidence = _copy_live_evidence(POST_CYCLE_READ, tmp_path / "evidence")
    memory_path = evidence / "live_memory_map.json"
    memory = json.loads(memory_path.read_text())
    memory["bytes"]["0x044F"] = 3
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    with pytest.raises(KnownDeviceStateError, match="does not confirm"):
        confirm_pending_persistence(evidence, state_path=state_path)
    assert load_known_state(state_path)["history"][0][
        "power_cycle_persistence"
    ] == "pending"


def test_incomplete_persistence_evidence_is_rejected(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    evidence = _copy_live_evidence(POST_CYCLE_READ, tmp_path / "evidence")
    transactions_path = evidence / "live_transactions.json"
    transactions = json.loads(transactions_path.read_text())
    transactions["read_count"] = 192
    transactions_path.write_text(json.dumps(transactions), encoding="utf-8")
    with pytest.raises(KnownDeviceStateError, match="193-read"):
        confirm_pending_persistence(evidence, state_path=state_path)


def test_wrong_device_persistence_evidence_is_rejected(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    evidence = _copy_live_evidence(POST_CYCLE_READ, tmp_path / "evidence")
    transactions_path = evidence / "live_transactions.json"
    transactions = json.loads(transactions_path.read_text())
    transactions["device"]["interface_number"] = "01"
    transactions_path.write_text(json.dumps(transactions), encoding="utf-8")
    with pytest.raises(KnownDeviceStateError, match="04d9:fc55"):
        confirm_pending_persistence(evidence, state_path=state_path)


def test_state_cli_commands_are_offline(tmp_path: Path, capsys):
    state_path = tmp_path / "known.json"
    _record_pending(state_path)
    assert main(["--state-file", str(state_path), "validate"]) == 0
    assert main(["--state-file", str(state_path), "show"]) == 0
    assert "valid known state" in capsys.readouterr().out
    source = (
        Path(__file__).parents[1]
        / "src" / "venus_profile" / "known_device_state.py"
    ).read_text()
    assert "readonly_hidraw" not in source
    assert "discover_venus_hidraw" not in source


def test_successful_restoration_updates_state_pending_after_readback(
    tmp_path: Path,
):
    state_path = tmp_path / "known.json"
    _write_confirmed_medium_state(state_path)
    backend = RestorationBackend([0x02, 0x03])
    from venus_profile.guarded_brightness import GuardedBrightnessTransport
    transport = GuardedBrightnessTransport(
        _device(),
        backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )
    output = execute_medium_to_high(
        _device(),
        output_root=tmp_path / "results",
        state_path=state_path,
        transport_factory=lambda device: transport,
        clock=lambda: datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc),
    )
    state = load_known_state(state_path)
    assert len(state["history"]) == 2
    latest = state["history"][-1]
    assert latest["previous_value"] == 2
    assert latest["confirmed_new_value"] == 3
    assert latest["power_cycle_persistence"] == "pending"
    operation = json.loads((output / "operation.json").read_text())
    assert operation["immediate_read_back_confirmed"] is True
    assert operation["power_cycle_persistence"] == "pending"


def test_failed_restoration_readback_does_not_update_known_state(tmp_path: Path):
    state_path = tmp_path / "known.json"
    _write_confirmed_medium_state(state_path)
    original = state_path.read_bytes()
    backend = RestorationBackend([0x02, 0x02])
    from venus_profile.guarded_brightness import GuardedBrightnessTransport
    transport = GuardedBrightnessTransport(
        _device(),
        backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )
    with pytest.raises(GuardedBrightnessError, match="read-back mismatch"):
        execute_medium_to_high(
            _device(),
            output_root=tmp_path / "results",
            state_path=state_path,
            transport_factory=lambda device: transport,
            clock=lambda: datetime(2026, 7, 27, 11, 1, tzinfo=timezone.utc),
        )
    assert state_path.read_bytes() == original
