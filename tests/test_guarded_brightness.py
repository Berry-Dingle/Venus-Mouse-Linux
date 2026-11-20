from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from venus_profile.guarded_brightness import (
    BRIGHTNESS_ADDRESS,
    LIGHTING_FINALISATION_REPORT,
    BrightnessValue,
    GuardedBrightnessError,
    GuardedBrightnessTransport,
    build_brightness_report,
    dry_run_lines,
    execute_brightness_change,
    execute_high_to_medium,
    execute_medium_to_high,
    main,
    safety_audit,
    validate_guarded_write_report,
)
from venus_profile.readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    build_f2_request,
    validate_f2_request,
)


def _device(interface: int = 2) -> HidrawDevice:
    return HidrawDevice(
        path=Path("/mock/hidraw4"),
        sysfs_path=Path("/mock/sysfs/interface2"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=interface,
        usage_page=0xFFA0,
        usage=1,
    )


def _response(value: int) -> bytes:
    return bytes.fromhex("02 08 4e 64 01 00 fa fa") + bytes([value])


class MockBrightnessIo:
    def __init__(
        self,
        values: list[int],
        *,
        fail_brightness: bool = False,
        fail_finalisation: bool = False,
    ) -> None:
        self.responses = [_response(value) for value in values]
        self.f2_requests: list[bytes] = []
        self.write_reports: list[bytes] = []
        self.fail_brightness = fail_brightness
        self.fail_finalisation = fail_finalisation
        self.closed = False

    def send_f2(self, request: bytes) -> None:
        validate_f2_request(request)
        self.f2_requests.append(request)

    def get_feature(self, report_id: int, length: int) -> bytes:
        assert (report_id, length) == (2, 16)
        return self.responses.pop(0)

    def send_brightness(self, value: BrightnessValue) -> bytes:
        if self.fail_brightness:
            raise GuardedBrightnessError("mock F3 ioctl failure")
        report = build_brightness_report(value)
        self.write_reports.append(report)
        return report

    def finalise_lighting(self) -> bytes:
        if self.fail_finalisation:
            raise GuardedBrightnessError("mock F1 ioctl failure")
        self.write_reports.append(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT

    def close(self) -> None:
        self.closed = True


def _transport(backend: MockBrightnessIo) -> GuardedBrightnessTransport:
    return GuardedBrightnessTransport(
        _device(),
        backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )


def test_exact_f3_medium_report_construction():
    assert build_brightness_report(BrightnessValue.MEDIUM) == bytes.fromhex(
        "02 f3 4f 04 01 00 00 00 02 00 00 00 00 00 00 00"
    )


def test_exact_f3_high_report_construction():
    assert build_brightness_report(BrightnessValue.HIGH) == bytes.fromhex(
        "02 f3 4f 04 01 00 00 00 03 00 00 00 00 00 00 00"
    )


@pytest.mark.parametrize("address", [0, 0x044E, 0x0450, 0x0478, 0x1417, 0xFFFF])
def test_rejects_every_other_address(address: int):
    report = bytearray(build_brightness_report(BrightnessValue.MEDIUM))
    report[2:4] = address.to_bytes(2, "little")
    with pytest.raises(GuardedBrightnessError, match="exact guarded"):
        validate_guarded_write_report(bytes(report))


@pytest.mark.parametrize("value", [-1, 0, 1, 4, 255, 256])
def test_rejects_every_other_value(value: int):
    with pytest.raises(GuardedBrightnessError, match="Medium 02 or High 03"):
        build_brightness_report(value)


def test_rejects_malformed_f1_and_all_f5_reports():
    malformed_f1 = bytearray(LIGHTING_FINALISATION_REPORT)
    malformed_f1[3] = 0x01
    exact_captured_f5 = bytes.fromhex(
        "02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    malformed_f5 = bytearray(exact_captured_f5)
    malformed_f5[-1] = 1
    for report in (bytes(malformed_f1), exact_captured_f5, bytes(malformed_f5)):
        with pytest.raises(GuardedBrightnessError, match="exact guarded"):
            validate_guarded_write_report(report)


def test_source_high_allows_high_to_medium_and_reads_back():
    backend = MockBrightnessIo([0x03, 0x02])
    transport = _transport(backend)
    plans = []
    result = transport.high_to_medium(before_write=plans.append)
    assert plans[0].original == BrightnessValue.HIGH
    assert result.read_back == BrightnessValue.MEDIUM
    assert backend.f2_requests == [
        build_f2_request(BRIGHTNESS_ADDRESS, 1),
        build_f2_request(BRIGHTNESS_ADDRESS, 1),
    ]
    assert backend.write_reports == [
        build_brightness_report(BrightnessValue.MEDIUM),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_source_medium_allows_restoration_to_high():
    backend = MockBrightnessIo([0x02, 0x03])
    transport = _transport(backend)
    result = transport.medium_to_high(before_write=lambda plan: None)
    assert result.read_back == BrightnessValue.HIGH
    assert backend.write_reports == [
        build_brightness_report(BrightnessValue.HIGH),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_source_high_rejects_restoration_as_already_restored():
    backend = MockBrightnessIo([0x03])
    with pytest.raises(GuardedBrightnessError, match="already 0x03"):
        _transport(backend).medium_to_high(before_write=lambda plan: None)
    assert backend.write_reports == []


def test_source_medium_rejects_high_to_medium_as_already_changed():
    backend = MockBrightnessIo([0x02])
    with pytest.raises(GuardedBrightnessError, match="already 0x02"):
        _transport(backend).high_to_medium(before_write=lambda plan: None)
    assert backend.write_reports == []


def test_unexpected_source_rejects_before_write():
    backend = MockBrightnessIo([0x04])
    with pytest.raises(GuardedBrightnessError, match="unexpected brightness"):
        _transport(backend).high_to_medium(before_write=lambda plan: None)
    assert backend.write_reports == []


def test_same_source_and_target_rejected_before_read_or_write():
    backend = MockBrightnessIo([])
    with pytest.raises(GuardedBrightnessError, match="equal"):
        _transport(backend).change_brightness(
            BrightnessValue.HIGH,
            BrightnessValue.HIGH,
            before_write=lambda plan: None,
        )
    assert backend.f2_requests == []
    assert backend.write_reports == []


def test_wrong_interface_rejected():
    with pytest.raises(GuardedBrightnessError, match="interface 02"):
        GuardedBrightnessTransport(_device(interface=1))


def test_permission_error_occurs_before_confirmation():
    confirmations = []
    executions = []
    with pytest.raises(HidrawPermissionError, match="read/write"):
        main(
            [
                "--live-brightness-write", "--high-to-medium",
                "--acknowledge-device-write",
            ],
            discovery=_device,
            permission_check=lambda path: False,
            input_fn=lambda prompt: confirmations.append(prompt) or "",
            executor=lambda *args, **kwargs: executions.append(args),
        )
    assert confirmations == []
    assert executions == []


def test_typed_confirmation_mismatch_never_executes(capsys):
    executions = []
    result = main(
        [
            "--live-brightness-write", "--high-to-medium",
            "--acknowledge-device-write",
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "write 044f 03 to 02",
        executor=lambda *args, **kwargs: executions.append(args),
    )
    assert result == 2
    assert executions == []
    assert "confirmation mismatch" in capsys.readouterr().err


def test_live_flags_and_exact_confirmation_execute(tmp_path: Path):
    calls = []
    result = main(
        [
            "--live-brightness-write", "--high-to-medium",
            "--acknowledge-device-write", "--output-root", str(tmp_path),
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 044F 03 TO 02",
        executor=lambda device, **kwargs: (
            calls.append((device, kwargs)) or kwargs["output_root"] / "result"
        ),
    )
    assert result == 0
    assert calls[0][0] == _device()
    assert calls[0][1]["expected"] == BrightnessValue.HIGH
    assert calls[0][1]["target"] == BrightnessValue.MEDIUM
    assert calls[0][1]["output_root"] == tmp_path


def test_exact_restoration_confirmation_executes(tmp_path: Path):
    calls = []
    result = main(
        [
            "--live-brightness-write", "--medium-to-high",
            "--acknowledge-device-write", "--output-root", str(tmp_path),
            "--state-file", str(tmp_path / "known.json"),
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 044F 02 TO 03",
        executor=lambda device, **kwargs: (
            calls.append((device, kwargs)) or tmp_path / "result"
        ),
    )
    assert result == 0
    assert calls[0][1]["expected"] == BrightnessValue.MEDIUM
    assert calls[0][1]["target"] == BrightnessValue.HIGH


def test_restoration_confirmation_mismatch_never_executes(tmp_path: Path):
    calls = []
    result = main(
        [
            "--live-brightness-write", "--medium-to-high",
            "--acknowledge-device-write", "--state-file",
            str(tmp_path / "known.json"),
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 044F 03 TO 02",
        executor=lambda *args, **kwargs: calls.append(1),
    )
    assert result == 2
    assert calls == []


def test_directions_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main([
            "--live-brightness-write",
            "--high-to-medium",
            "--medium-to-high",
            "--acknowledge-device-write",
        ])


def test_missing_acknowledgement_or_direction_never_discovers():
    discoveries = []
    for args in (
        ["--live-brightness-write", "--high-to-medium"],
        ["--live-brightness-write", "--acknowledge-device-write"],
    ):
        assert main(args, discovery=lambda: discoveries.append(1)) == 2
    assert discoveries == []


def test_f3_ioctl_failure_has_no_finalisation_retry_or_restore():
    backend = MockBrightnessIo([0x03], fail_brightness=True)
    transport = _transport(backend)
    with pytest.raises(GuardedBrightnessError, match="F3 ioctl failure"):
        transport.high_to_medium(before_write=lambda plan: None)
    assert backend.write_reports == []
    assert backend.f2_requests == [build_f2_request(BRIGHTNESS_ADDRESS, 1)]


def test_finalisation_failure_has_one_write_and_no_retry_or_restore():
    backend = MockBrightnessIo([0x03], fail_finalisation=True)
    transport = _transport(backend)
    with pytest.raises(GuardedBrightnessError, match="F1 ioctl failure"):
        transport.high_to_medium(before_write=lambda plan: None)
    assert backend.write_reports == [
        build_brightness_report(BrightnessValue.MEDIUM)
    ]
    assert len(backend.f2_requests) == 1


def test_mismatched_readback_fails_without_retry_or_restore():
    backend = MockBrightnessIo([0x03, 0x03])
    transport = _transport(backend)
    with pytest.raises(GuardedBrightnessError, match="no retry or restoration"):
        transport.high_to_medium(before_write=lambda plan: None)
    assert backend.write_reports == [
        build_brightness_report(BrightnessValue.MEDIUM),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert len(backend.f2_requests) == 2


def test_default_and_dry_run_never_discover_or_open(capsys):
    discoveries = []
    assert main([], discovery=lambda: discoveries.append(1)) == 0
    assert main(["--dry-run"], discovery=lambda: discoveries.append(1)) == 0
    assert discoveries == []
    output = capsys.readouterr().out
    assert "no hidraw" in output
    assert "02 f3 4f 04 01 00 00 00 02" in output


def test_dry_run_is_exact_and_contains_no_f5():
    lines = dry_run_lines()
    assert "02 f2 4f 04 01 00" in lines[2]
    assert "02 f3 4f 04 01 00 00 00 02" in lines[3]
    assert LIGHTING_FINALISATION_REPORT.hex(" ") in lines[5]
    assert all("02 f5" not in line.lower() for line in lines)


def test_deterministic_success_artifacts(tmp_path: Path):
    fixed = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    backend = MockBrightnessIo([0x03, 0x02])
    transport = _transport(backend)
    output = execute_high_to_medium(
        _device(),
        output_root=tmp_path,
        transport_factory=lambda device: transport,
        clock=lambda: fixed,
        state_path=tmp_path / "known.json",
    )
    assert output.name == "brightness_044f_20260727T100000.000000Z"
    assert sorted(path.name for path in output.iterdir()) == [
        "operation.json",
        "postwrite_read.json",
        "prewrite_read.json",
        "reports_sent.tsv",
        "result.md",
    ]
    operation = json.loads((output / "operation.json").read_text())
    assert operation["status"] == "success"
    assert operation["original_hex"] == "03"
    assert operation["target_hex"] == "02"
    assert operation["automatic_retry"] is False
    assert operation["automatic_restoration"] is False
    assert operation["immediate_read_back_confirmed"] is True
    assert operation["power_cycle_persistence"] == "pending"
    sent = (output / "reports_sent.tsv").read_text()
    assert sent.count("\n") == 5
    assert "prewrite-f2" in sent
    assert "brightness-f3" in sent
    assert "lighting-f1" in sent
    assert "postwrite-f2" in sent
    assert backend.closed


def test_failed_readback_artifacts_show_failed_closed(tmp_path: Path):
    fixed = datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc)
    backend = MockBrightnessIo([0x03, 0x03])
    with pytest.raises(GuardedBrightnessError, match="read-back mismatch"):
        execute_high_to_medium(
            _device(),
            output_root=tmp_path,
            transport_factory=lambda device: _transport(backend),
            clock=lambda: fixed,
            state_path=tmp_path / "known.json",
        )
    output = next(tmp_path.iterdir())
    assert "failed closed" in (output / "result.md").read_text()
    assert not (output / "postwrite_read.json").exists()
    assert backend.closed


def test_static_safety_audit():
    results = safety_audit()
    assert len(results) == 7
    assert any("0x044F" in result for result in results)
    assert any("no F5" in result for result in results)
    assert any("no automatic retry" in result for result in results)
