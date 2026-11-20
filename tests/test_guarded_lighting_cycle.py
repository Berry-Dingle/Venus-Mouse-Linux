from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from venus_profile.device_session import DeviceSession
from venus_profile.guarded_lighting_cycle import (
    ALLOWED_PREFIXES,
    FULL_BRIGHTNESS_PREFIX,
    INITIAL_CONFIRMATION,
    INTER_COMMAND_DELAY_SECONDS,
    LIGHTING_FINALISATION_REPORT,
    STAGES,
    GuardedLightingCycleError,
    GuardedLightingCycleTransport,
    build_stage_report,
    dry_run_lines,
    main,
    run_cycle,
    safety_audit,
    validate_outgoing_report,
)
from venus_profile.readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    ReadOnlyHidrawTransport,
    build_f2_request,
)


def _device(
    *,
    interface: int = 2,
    usage_page: int = 0xFFA0,
    usage: int = 1,
) -> HidrawDevice:
    return HidrawDevice(
        Path("/mock/hidraw4"),
        Path("/mock/sysfs/interface2"),
        0x04D9,
        0xFC55,
        interface,
        usage_page,
        usage,
    )


def _response(prefix: bytes) -> bytes:
    return bytes.fromhex("02 08 48 64 06 00 fa fa") + prefix


class MockCycleIo:
    def __init__(self, prefixes: list[bytes], *, fail_f3=False, fail_f1=False):
        self.responses = [_response(prefix) for prefix in prefixes]
        self.events: list[tuple[str, object]] = []
        self.writes: list[bytes] = []
        self.closed = False
        self.fail_f3 = fail_f3
        self.fail_f1 = fail_f1

    def send_f2(self, request: bytes) -> None:
        self.events.append(("f2-send", request))

    def get_feature(self, report_id: int, length: int) -> bytes:
        self.events.append(("f2-get", (report_id, length)))
        return self.responses.pop(0)

    def send_stage(self, stage) -> bytes:
        if self.fail_f3:
            raise GuardedLightingCycleError("mock F3 failure")
        report = build_stage_report(stage)
        self.events.append(("f3", report))
        self.writes.append(report)
        return report

    def finalise_lighting(self) -> bytes:
        if self.fail_f1:
            raise GuardedLightingCycleError("mock F1 failure")
        self.events.append(("f1", LIGHTING_FINALISATION_REPORT))
        self.writes.append(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT

    def close(self) -> None:
        self.events.append(("close", None))
        self.closed = True


def _transport(backend: MockCycleIo, *, sleeper=lambda seconds: None):
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: backend
        ),
    )
    return GuardedLightingCycleTransport(
        session,
        backend_factory=lambda path: backend,
        sleeper=sleeper,
    )


def test_fixed_seven_stage_plan_is_exact():
    assert [(stage.number, stage.name, stage.prefix) for stage in STAGES] == [
        (1, "Wave", bytes.fromhex("7b 2d d2 03 00 01")),
        (2, "Flash", bytes.fromhex("7b 2d d2 04 05 01")),
        (3, "Breathing Low", bytes.fromhex("7b 2d d2 02 08 01")),
        (4, "Breathing Medium", bytes.fromhex("7b 2d d2 02 05 01")),
        (5, "Breathing High", bytes.fromhex("7b 2d d2 02 02 01")),
        (6, "Breathing UI Full Brightness", bytes.fromhex("7b 2d d2 02 00 01")),
        (7, "Restore Full Brightness", FULL_BRIGHTNESS_PREFIX),
    ]
    assert ALLOWED_PREFIXES == frozenset(stage.prefix for stage in STAGES)


def test_exact_f3_and_f1_reports_are_constructed():
    assert build_stage_report(STAGES[0]) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 7b 2d d2 03 00 01 00 00"
    )
    assert build_stage_report(STAGES[-1]) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00"
    )
    assert LIGHTING_FINALISATION_REPORT == bytes.fromhex(
        "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert validate_outgoing_report(LIGHTING_FINALISATION_REPORT) == (
        "lighting-finalisation"
    )


@pytest.mark.parametrize("mutator", [
    lambda report: report[:-1],
    lambda report: report[:8] + bytes.fromhex("7b 2d d2 02 05 02") + report[14:],
    lambda report: report[:2] + bytes([0xF2]) + report[3:],
    lambda report: report[:14] + b"\x01\x00",
])
def test_malformed_or_non_plan_reports_are_rejected(mutator):
    with pytest.raises(GuardedLightingCycleError, match="rejected|16 bytes"):
        validate_outgoing_report(mutator(build_stage_report(STAGES[0])))


def test_transport_orders_f2_f3_f1_f2_and_two_ten_ms_delays():
    backend = MockCycleIo([FULL_BRIGHTNESS_PREFIX, STAGES[0].prefix])
    delays: list[float] = []
    transport = _transport(backend, sleeper=delays.append)
    assert transport.write_stage(STAGES[0]) == STAGES[0].prefix
    assert backend.events == [
        ("f2-send", build_f2_request(0x0449, 6)),
        ("f2-get", (2, 16)),
        ("f3", build_stage_report(STAGES[0])),
        ("f1", LIGHTING_FINALISATION_REPORT),
        ("f2-send", build_f2_request(0x0449, 6)),
        ("f2-get", (2, 16)),
    ]
    assert delays == [INTER_COMMAND_DELAY_SECONDS, INTER_COMMAND_DELAY_SECONDS]
    assert transport.reports_sent == [
        ("prewrite-f2", build_f2_request(0x0449, 6)),
        ("stage-1-f3", build_stage_report(STAGES[0])),
        ("stage-1-f1", LIGHTING_FINALISATION_REPORT),
        ("postwrite-f2", build_f2_request(0x0449, 6)),
    ]


def test_identity_and_starting_prefix_are_required():
    with pytest.raises(GuardedLightingCycleError, match="04D9:FC55"):
        GuardedLightingCycleTransport(_device(interface=1))

    backend = MockCycleIo([bytes.fromhex("7b 2d d2 09 00 01")])
    transport = _transport(backend)
    with pytest.raises(GuardedLightingCycleError, match="unexpected lighting prefix"):
        transport.write_stage(STAGES[1])
    assert backend.writes == []


def test_capability_failure_happens_before_writer_creation():
    backend = MockCycleIo([FULL_BRIGHTNESS_PREFIX, STAGES[0].prefix])
    transport = _transport(backend)
    transport._session._request_internal_capability = lambda capability: (
        (_ for _ in ()).throw(GuardedLightingCycleError("capability denied"))
    )
    with pytest.raises(GuardedLightingCycleError, match="capability denied"):
        transport.write_stage(STAGES[0])
    assert backend.writes == []


def test_readback_mismatch_fails_closed_without_retry():
    backend = MockCycleIo([FULL_BRIGHTNESS_PREFIX, STAGES[1].prefix])
    transport = _transport(backend)
    with pytest.raises(GuardedLightingCycleError, match="read-back mismatch"):
        transport.write_stage(STAGES[0])
    assert backend.writes == [build_stage_report(STAGES[0]), LIGHTING_FINALISATION_REPORT]
    assert len([event for event in backend.events if event[0] == "f3"]) == 1


def test_transport_close_closes_writer_and_reader_once():
    backend = MockCycleIo([FULL_BRIGHTNESS_PREFIX])
    transport = _transport(backend)
    transport.read_prefix()
    transport.close()
    transport.close()
    assert backend.closed
    assert [event[0] for event in backend.events].count("close") == 1


class ScriptedTransport:
    def __init__(self, *, fail_stage: int | None = None):
        self.current = FULL_BRIGHTNESS_PREFIX
        self.fail_stage = fail_stage
        self.reports_sent: list[tuple[str, bytes]] = []
        self.calls: list[str] = []
        self.closed = False

    def read_prefix(self) -> bytes:
        self.calls.append("read")
        return self.current

    def write_stage(self, stage) -> bytes:
        self.calls.append(f"write-{stage.number}")
        self.current = stage.prefix
        self.reports_sent.append((f"stage-{stage.number}-f3", build_stage_report(stage)))
        if self.fail_stage == stage.number:
            raise RuntimeError("scripted stage exception")
        return stage.prefix

    def restore(self) -> tuple[bool, str]:
        self.calls.append("restore")
        self.current = FULL_BRIGHTNESS_PREFIX
        return True, "restored by fake"

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


def _run_scripted(tmp_path: Path, answers: list[str], *, fail_stage=None):
    fake = ScriptedTransport(fail_stage=fail_stage)
    fixed = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    iterator = iter(answers)
    return fake, run_cycle(
        _device(),
        output_root=tmp_path,
        input_fn=lambda prompt: next(iterator),
        clock=lambda: fixed,
        transport_factory=lambda device: fake,
    )


def _answers(count: int, *, final: str = "Y") -> list[str]:
    values = [INITIAL_CONFIRMATION]
    for index in range(count):
        values.extend(["", final])
    return values


def test_success_artifacts_contain_all_stage_and_report_fields(tmp_path: Path):
    fake, output = _run_scripted(tmp_path, _answers(len(STAGES)))
    operation = json.loads((output / "operation.json").read_text())
    assert operation == {
        "started": "2026-07-28T12:00:00+00:00",
        "completed": "2026-07-28T12:00:00+00:00",
        "status": "completed",
        "error": None,
        "device": _device().to_dict(),
        "address_start_hex": "0x0449",
        "address_end_hex": "0x044E",
        "brightness_address_excluded": "0x044F",
        "stages_completed": 7,
        "stage_count": 7,
        "restoration_succeeded": True,
        "restoration_detail": "final stage confirmed Full Brightness",
        "power_cycle_persistence": "not-established",
    }
    stage_lines = (output / "stage_results.tsv").read_text().splitlines()
    assert len(stage_lines) == 8
    assert "Breathing High" in stage_lines[5]
    report_lines = (output / "reports_sent.tsv").read_text().splitlines()
    assert report_lines[0] == "sequence\toperation\treport_id\tpayload"
    assert any("stage-1-f3" in line and "02 f3 49 04 06" in line for line in report_lines)
    result = (output / "result.md").read_text()
    assert "Status: completed" in result
    assert "Persistence: not established" in result
    assert fake.closed


@pytest.mark.parametrize("failure", ["abort", "visual", "exception", "keyboard"])
def test_failures_attempt_guarded_restoration_and_record_failure_artifacts(
    tmp_path: Path, failure: str
):
    if failure == "abort":
        answers = [INITIAL_CONFIRMATION, "", "Y", "Q"]
    elif failure == "visual":
        answers = [INITIAL_CONFIRMATION, "", "N", "wrong colour"]
    elif failure == "keyboard":
        prompts = 0
        def interrupt(prompt):
            nonlocal prompts
            prompts += 1
            if prompts == 3:
                raise KeyboardInterrupt
            return INITIAL_CONFIRMATION if prompts == 1 else ""
        answers = None
    else:
        answers = [INITIAL_CONFIRMATION, "", "Y"]

    fake = ScriptedTransport(fail_stage=2 if failure == "exception" else None)
    fixed = datetime(2026, 7, 28, 12, 1, tzinfo=timezone.utc)
    iterator = iter(answers or [])
    input_fn = interrupt if failure == "keyboard" else lambda prompt: next(iterator)
    with pytest.raises(GuardedLightingCycleError):
        run_cycle(
            _device(),
            output_root=tmp_path,
            input_fn=input_fn,
            clock=lambda: fixed,
            transport_factory=lambda device: fake,
        )
    output = next(tmp_path.iterdir())
    operation = json.loads((output / "operation.json").read_text())
    assert operation["status"] == "aborted"
    assert operation["restoration_succeeded"] is True
    assert operation["power_cycle_persistence"] == "not-established"
    assert "Restored Full Brightness: True" in (output / "result.md").read_text()
    assert "restore" in fake.calls
    assert fake.closed


def test_offline_modes_never_discover_or_run(capsys):
    def forbidden_discovery():
        pytest.fail("offline mode attempted discovery")

    assert main([], discovery=forbidden_discovery) == 0
    assert main(["--dry-run"], discovery=forbidden_discovery) == 0
    assert main(["--safety-audit"], discovery=forbidden_discovery) == 0
    assert "no hidraw" in capsys.readouterr().out


def test_live_barriers_require_explicit_mode_and_acknowledgement(capsys):
    discovered = []
    assert main(
        ["--live-lighting-cycle"],
        discovery=lambda: discovered.append(True),
    ) == 2
    assert discovered == []
    assert "acknowledge-device-writes" in capsys.readouterr().err


def test_static_safety_audit_is_directly_covered():
    results = safety_audit()
    assert len(results) == 8
    assert any("fixed immutable" in result for result in results)
    assert any("0x044F" in result for result in results)
    assert any("F5" in result for result in results)
    assert any("immediate read-back" in result for result in results)
