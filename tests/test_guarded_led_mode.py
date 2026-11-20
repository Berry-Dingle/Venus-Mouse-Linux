from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from venus_profile.guarded_led_mode import (
    LIGHTING_FINALISATION_REPORT,
    GuardedLedModeError,
    GuardedLedModeTransport,
    LedModeState,
    build_led_mode_report,
    dry_run_lines,
    execute_led_mode_change,
    main,
    safety_audit,
    validate_guarded_led_mode_report,
)
from venus_profile.device_session import DeviceSession
from venus_profile.known_led_mode_state import (
    BREATHING_PREFIX,
    FULL_BRIGHTNESS_PREFIX,
    TRUE_OFF_PREFIX,
    CURRENT_FULL_BRIGHTNESS_PREFIX,
    RAINBOW_PREFIX,
    KnownLedModeStateError,
    atomic_write_led_mode_state,
    confirm_led_mode_persistence,
    correct_led_mode_persistence,
    initial_state,
    load_led_mode_state,
    main as state_main,
    record_led_mode_transition,
)
from venus_profile.readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    ReadOnlyHidrawTransport,
    validate_f2_request,
)
from evidence_paths import EVIDENCE  # noqa: E402


POST_FULL_READ = (
    EVIDENCE / "usb-captures" / "live-reads"
    / "venus_read_20260727T101703.627495Z"
)


def _device(
    *,
    interface: int = 2,
    usage_page: int = 0xFFA0,
) -> HidrawDevice:
    return HidrawDevice(
        path=Path("/mock/hidraw4"),
        sysfs_path=Path("/mock/sysfs"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=interface,
        usage_page=usage_page,
        usage=1,
    )


def _response(prefix: bytes) -> bytes:
    return (
        bytes.fromhex("02 08 48 64 06 00 fa fa")
        + prefix + b"\x00\x00"
    )


class MockModeIo:
    def __init__(
        self,
        prefixes: list[bytes],
        *,
        fail_f3: bool = False,
        fail_f1: bool = False,
    ):
        self.responses = [_response(item) for item in prefixes]
        self.fail_f3 = fail_f3
        self.fail_f1 = fail_f1
        self.writes: list[bytes] = []
        self.f2: list[bytes] = []
        self.closed = False

    def send_f2(self, request: bytes) -> None:
        validate_f2_request(request)
        self.f2.append(request)

    def get_feature(self, report_id: int, length: int) -> bytes:
        assert (report_id, length) == (2, 16)
        return self.responses.pop(0)

    def send_led_mode(self, state: LedModeState) -> bytes:
        if self.fail_f3:
            raise GuardedLedModeError("mock F3 failure")
        report = build_led_mode_report(state)
        self.writes.append(report)
        return report

    def finalise_lighting(self) -> bytes:
        if self.fail_f1:
            raise GuardedLedModeError("mock F1 failure")
        self.writes.append(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT

    def close(self) -> None:
        self.closed = True


def _transport(backend: MockModeIo) -> GuardedLedModeTransport:
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: backend
        ),
    )
    return GuardedLedModeTransport(
        session, backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )


def _state(path: Path) -> Path:
    atomic_write_led_mode_state(
        path, initial_state("2026-07-27T10:17:03+00:00")
    )
    return path


def _confirmed_breathing_state(path: Path) -> Path:
    _state(path)
    state = record_led_mode_transition(
        state_path=path,
        previous_prefix=FULL_BRIGHTNESS_PREFIX,
        new_prefix=BREATHING_PREFIX,
        operation_timestamp="2026-07-27T11:03:22+00:00",
        result_directory=path.parent / "full-to-breathing",
        updated_at="2026-07-27T11:03:23+00:00",
    )
    state["history"][0]["power_cycle_persistence"] = "confirmed"
    state["history"][0]["persistence_confirmed_at"] = (
        "2026-07-27T11:06:07+00:00"
    )
    state["history"][0]["latest_confirming_live_read_directory"] = (
        "/saved/complete/live-read"
    )
    atomic_write_led_mode_state(path, state)
    return path


def test_exact_semantic_prefixes_and_f3_construction():
    assert LedModeState.FULL_BRIGHTNESS.value == bytes.fromhex(
        "ff 00 00 01 00 01"
    )
    assert LedModeState.BREATHING.value == bytes.fromhex(
        "ff 00 00 02 05 01"
    )
    assert build_led_mode_report(LedModeState.FULL_BRIGHTNESS) == (
        bytes.fromhex(
            "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00"
        )
    )
    assert build_led_mode_report(LedModeState.BREATHING) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 ff 00 00 02 05 01 00 00"
    )
    report = build_led_mode_report(LedModeState.BREATHING)
    address = int.from_bytes(report[2:4], "little")
    length = int.from_bytes(report[4:6], "little")
    assert (address, address + length - 1) == (0x0449, 0x044E)
    assert address + length == 0x044F


def test_true_off_to_full_exact_prefix_and_report():
    assert LedModeState.TRUE_OFF.value == bytes.fromhex("7b 2d d2 00 00 00")
    assert LedModeState.CURRENT_FULL_BRIGHTNESS.value == bytes.fromhex(
        "7b 2d d2 01 00 01"
    )
    assert build_led_mode_report(LedModeState.CURRENT_FULL_BRIGHTNESS) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00"
    )
    assert validate_guarded_led_mode_report(
        build_led_mode_report(LedModeState.TRUE_OFF)
    ) == "profile-1-led-mode-prefix"


def test_rainbow_to_full_uses_complete_triplet_and_exact_report():
    assert LedModeState.RAINBOW.value == bytes.fromhex("7b 2d d2 02 00 03")
    assert build_led_mode_report(LedModeState.RAINBOW) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 7b 2d d2 02 00 03 00 00"
    )
    assert validate_guarded_led_mode_report(
        build_led_mode_report(LedModeState.RAINBOW)
    ) == "profile-1-led-mode-prefix"


def test_rainbow_to_full_transport_requires_triplet_and_preserves_rgb():
    backend = MockModeIo([RAINBOW_PREFIX, CURRENT_FULL_BRIGHTNESS_PREFIX])
    with _transport(backend) as transport:
        result = transport.rainbow_to_full(before_write=lambda plan: None)
    assert result.read_back == CURRENT_FULL_BRIGHTNESS_PREFIX
    assert backend.writes == [
        bytes.fromhex("02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00"),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert all(report[0] == 2 for report in backend.writes)
    assert all(b"044f" not in report for report in backend.writes)


def test_rainbow_to_full_execute_records_pending_history(tmp_path: Path):
    state_path = _state(tmp_path / "rainbow-state.json")
    state = load_led_mode_state(state_path)
    state["current_prefix"] = RAINBOW_PREFIX.hex(" ")
    state["current_semantic_mode"] = "Rainbow"
    state["current_observation"] = {
        "prefix": RAINBOW_PREFIX.hex(" "),
        "semantic_mode": "Rainbow",
        "source_of_authority": "controlled-windows-rainbow-post-read",
    }
    atomic_write_led_mode_state(state_path, state)
    backend = MockModeIo([RAINBOW_PREFIX, CURRENT_FULL_BRIGHTNESS_PREFIX])
    execute_led_mode_change(
        _device(), expected=LedModeState.RAINBOW,
        target=LedModeState.CURRENT_FULL_BRIGHTNESS,
        output_root=tmp_path / "results", state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc),
    )
    saved = load_led_mode_state(state_path)
    transition = saved["history"][-1]
    assert transition["previous_semantic_mode"] == "Rainbow"
    assert transition["new_semantic_mode"] == "Full Brightness"
    assert transition["previous_prefix"] == RAINBOW_PREFIX.hex(" ")
    assert transition["power_cycle_persistence"] == "pending"


def test_rainbow_persistence_correction_supersedes_pre_cycle_read(tmp_path: Path):
    source = (
        EVIDENCE / "usb-captures" / "device-state"
        / "known_led_mode_state.json"
    )
    state_path = tmp_path / "state.json"
    shutil.copyfile(source, state_path)
    transition_id = (
        "2026-07-27T14:57:53.873060+00:00|0x0449-0x044E|"
        "7b2dd2020003-to-7b2dd2010001"
    )
    seeded = json.loads(state_path.read_text(encoding="utf-8"))
    seeded_item = next(x for x in seeded["history"]
                       if x["transition_id"] == transition_id)
    seeded_item["latest_confirming_live_read_directory"] = str(
        EVIDENCE / "usb-captures" / "live-reads"
        / "venus_read_20260727T145814.527436Z"
    )
    seeded_item["persistence_confirmed_at"] = "2026-07-27T14:58:14+00:00"
    seeded.pop("audit_corrections", None)
    state_path.write_text(json.dumps(seeded), encoding="utf-8")
    corrected = correct_led_mode_persistence(
        transition_id=transition_id,
        superseded_live_read_directory=(
            EVIDENCE / "usb-captures" / "live-reads"
            / "venus_read_20260727T145814.527436Z"
        ),
        genuine_live_read_directory=(
            EVIDENCE / "usb-captures" / "live-reads"
            / "venus_read_20260727T150341.873939Z"
        ),
        state_path=state_path,
        clock=lambda: datetime(2026, 7, 27, 15, 10, tzinfo=timezone.utc),
    )
    item = next(x for x in corrected["history"]
                if x["transition_id"] == transition_id)
    assert item["power_cycle_persistence"] == "confirmed"
    assert item["latest_confirming_live_read_directory"].endswith(
        "venus_read_20260727T150341.873939Z"
    )
    assert item["superseded_persistence_evidence"]["status"] == (
        "post-write-full-read-not-power-cycle"
    )
    assert corrected["audit_corrections"][-1]["superseded_read"].endswith(
        "venus_read_20260727T145814.527436Z"
    )


def test_rainbow_source_and_full_target_are_not_identified_by_mode_byte_alone():
    backend = MockModeIo([bytes.fromhex("7b 2d d2 02 05 01")])
    with _transport(backend) as transport:
        with pytest.raises(GuardedLedModeError, match="unexpected lighting prefix"):
            transport.rainbow_to_full(before_write=lambda plan: None)


def test_rainbow_dry_run_is_offline(capsys):
    assert main(["--dry-run", "--rainbow-to-full"],
                discovery=lambda: pytest.fail("offline")) == 0
    output = capsys.readouterr().out
    assert "source: Rainbow: 7b 2d d2 02 00 03" in output
    assert "target: Full Brightness: 7b 2d d2 01 00 01" in output
    assert "02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00" in output


def test_off_to_full_dry_run_is_explicit_and_offline(capsys):
    assert main(["--dry-run", "--off-to-full"], discovery=lambda: pytest.fail("offline")) == 0
    output = capsys.readouterr().out
    assert "source: True Off: 7b 2d d2 00 00 00" in output
    assert "target: Full Brightness: 7b 2d d2 01 00 01" in output
    assert "02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00" in output


@pytest.mark.parametrize("index", range(16))
def test_every_mutated_f3_byte_is_rejected(index: int):
    report = bytearray(build_led_mode_report(LedModeState.BREATHING))
    report[index] ^= 0x80
    with pytest.raises(GuardedLedModeError):
        validate_guarded_led_mode_report(bytes(report))


def test_exact_f1_only_and_no_arbitrary_state():
    assert validate_guarded_led_mode_report(
        LIGHTING_FINALISATION_REPORT
    ) == "lighting-finalisation"
    malformed = bytearray(LIGHTING_FINALISATION_REPORT)
    malformed[3] = 5
    with pytest.raises(GuardedLedModeError):
        validate_guarded_led_mode_report(bytes(malformed))
    with pytest.raises(GuardedLedModeError):
        build_led_mode_report(b"arbitrary")  # type: ignore[arg-type]


def test_full_to_breathing_exact_readback_success():
    backend = MockModeIo([FULL_BRIGHTNESS_PREFIX, BREATHING_PREFIX])
    with _transport(backend) as transport:
        result = transport.full_to_breathing(before_write=lambda plan: None)
    assert result.read_back == BREATHING_PREFIX
    assert backend.writes == [
        build_led_mode_report(LedModeState.BREATHING),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert len(backend.f2) == 2
    assert backend.closed


def test_breathing_to_full_exact_readback_success():
    backend = MockModeIo([BREATHING_PREFIX, FULL_BRIGHTNESS_PREFIX])
    with _transport(backend) as transport:
        result = transport.breathing_to_full(before_write=lambda plan: None)
    assert result.read_back == FULL_BRIGHTNESS_PREFIX
    assert backend.writes == [
        bytes.fromhex(
            "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00"
        ),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert len(backend.f2) == 2


def test_reverse_rejects_full_as_already_changed_before_write():
    backend = MockModeIo([FULL_BRIGHTNESS_PREFIX])
    with pytest.raises(GuardedLedModeError, match="already Full Brightness"):
        with _transport(backend) as transport:
            transport.breathing_to_full(before_write=lambda plan: None)
    assert backend.writes == []


def test_reverse_rejects_any_unknown_source_prefix():
    backend = MockModeIo([bytes.fromhex("ff 00 00 02 04 01")])
    with pytest.raises(GuardedLedModeError, match="unexpected lighting prefix"):
        with _transport(backend) as transport:
            transport.breathing_to_full(before_write=lambda plan: None)
    assert backend.writes == []


def test_reverse_readback_mismatch_fails_without_retry_or_restore():
    backend = MockModeIo([BREATHING_PREFIX, BREATHING_PREFIX])
    with pytest.raises(GuardedLedModeError, match="read-back mismatch"):
        with _transport(backend) as transport:
            transport.breathing_to_full(before_write=lambda plan: None)
    assert backend.writes == [
        build_led_mode_report(LedModeState.FULL_BRIGHTNESS),
        LIGHTING_FINALISATION_REPORT,
    ]


@pytest.mark.parametrize(
    "source",
    [
        bytes.fromhex("ff 00 00 01 01 01"),
        BREATHING_PREFIX,
        bytes.fromhex("00 00 00 00 00 00"),
    ],
)
def test_source_prefix_mismatch_rejects_before_write(source: bytes):
    backend = MockModeIo([source])
    with pytest.raises(GuardedLedModeError):
        with _transport(backend) as transport:
            transport.full_to_breathing(before_write=lambda plan: None)
    assert backend.writes == []


def test_source_mismatch_rejects_before_write_capability_is_opened():
    reader = MockModeIo([bytes.fromhex("ff 00 00 01 01 01")])
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: reader
        ),
    )
    writer_opened = []
    transport = GuardedLedModeTransport(
        session,
        backend_factory=lambda path: writer_opened.append(path),
        sleeper=lambda seconds: None,
    )
    with pytest.raises(GuardedLedModeError):
        with transport:
            transport.full_to_breathing(before_write=lambda plan: None)
    assert writer_opened == []


def test_any_readback_byte_mismatch_fails_without_retry_or_restore():
    for index in range(6):
        wrong = bytearray(BREATHING_PREFIX)
        wrong[index] ^= 1
        backend = MockModeIo([FULL_BRIGHTNESS_PREFIX, bytes(wrong)])
        with pytest.raises(GuardedLedModeError):
            with _transport(backend) as transport:
                transport.full_to_breathing(before_write=lambda plan: None)
        assert backend.writes == [
            build_led_mode_report(LedModeState.BREATHING),
            LIGHTING_FINALISATION_REPORT,
        ]


@pytest.mark.parametrize(
    ("failure", "message", "write_count"),
    [("f3", "F3 failure", 0), ("f1", "F1 failure", 1)],
)
def test_write_and_finalisation_fail_closed(
    failure: str, message: str, write_count: int
):
    backend = MockModeIo(
        [FULL_BRIGHTNESS_PREFIX],
        fail_f3=failure == "f3",
        fail_f1=failure == "f1",
    )
    with pytest.raises(GuardedLedModeError, match=message):
        with _transport(backend) as transport:
            transport.full_to_breathing(before_write=lambda plan: None)
    assert len(backend.writes) == write_count


def test_wrong_interface_is_rejected():
    with pytest.raises(GuardedLedModeError, match="interface 02"):
        GuardedLedModeTransport(_device(interface=1))


def test_cli_permission_and_confirmation_barriers(capsys):
    assert main([], discovery=lambda: pytest.fail("must not discover")) == 0
    assert main(
        ["--dry-run", "--full-to-breathing"],
        discovery=lambda: pytest.fail("must not discover"),
    ) == 0


def test_permission_error_is_before_confirmation():
    prompts = []
    with pytest.raises(HidrawPermissionError):
        main(
            [
                "--live-led-mode-write", "--full-to-breathing",
                "--acknowledge-device-write",
            ],
            discovery=_device,
            permission_check=lambda path: False,
            input_fn=lambda prompt: prompts.append(prompt) or "",
        )
    assert prompts == []


def test_confirmation_must_be_exact_and_executor_not_called():
    calls = []
    result = main(
        [
            "--live-led-mode-write", "--full-to-breathing",
            "--acknowledge-device-write",
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 FULL TO BREATHING ",
        executor=lambda *args, **kwargs: calls.append(True),
    )
    assert result == 2
    assert calls == []


def test_exact_confirmation_reaches_executor(tmp_path: Path):
    calls = []
    assert main(
        [
            "--live-led-mode-write", "--full-to-breathing",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: (
            calls.append(prompt) or "WRITE 0449 FULL TO BREATHING"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert calls == ["Type exactly WRITE 0449 FULL TO BREATHING: "]


def test_reverse_live_direction_requires_exact_confirmation(tmp_path: Path):
    calls = []
    assert main(
        [
            "--live-led-mode-write", "--breathing-to-full",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: (
            calls.append(prompt) or "WRITE 0449 BREATHING TO FULL"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert calls == ["Type exactly WRITE 0449 BREATHING TO FULL: "]


def test_reverse_confirmation_mismatch_never_executes():
    calls = []
    assert main(
        [
            "--live-led-mode-write", "--breathing-to-full",
            "--acknowledge-device-write",
        ],
        discovery=_device,
        permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 FULL TO BREATHING",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []


def test_success_artifacts_and_separate_state_history(tmp_path: Path):
    state_path = _state(tmp_path / "led-state.json")
    brightness_state = (
        EVIDENCE / "usb-captures" / "device-state"
        / "known_device_state.json"
    )
    brightness_hash = hashlib.sha256(brightness_state.read_bytes()).hexdigest()
    backend = MockModeIo([FULL_BRIGHTNESS_PREFIX, BREATHING_PREFIX])
    fixed = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    output = execute_led_mode_change(
        _device(),
        expected=LedModeState.FULL_BRIGHTNESS,
        target=LedModeState.BREATHING,
        output_root=tmp_path / "results",
        state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: fixed,
    )
    assert {item.name for item in output.iterdir()} == {
        "operation.json", "reports_sent.tsv", "prewrite_read.json",
        "postwrite_read.json", "result.md",
    }
    operation = json.loads((output / "operation.json").read_text())
    assert operation["previous_prefix"] == "ff 00 00 01 00 01"
    assert operation["target_prefix"] == "ff 00 00 02 05 01"
    assert operation["immediate_read_back_confirmed"] is True
    assert operation["power_cycle_persistence"] == "pending"
    state = load_led_mode_state(state_path)
    assert len(state["history"]) == 1
    assert state["current_prefix"] == "ff 00 00 02 05 01"
    assert state["history"][0]["power_cycle_persistence"] == "pending"
    assert hashlib.sha256(brightness_state.read_bytes()).hexdigest() == (
        brightness_hash
    )


def test_reverse_success_preserves_confirmed_forward_history(tmp_path: Path):
    state_path = _confirmed_breathing_state(tmp_path / "led-state.json")
    backend = MockModeIo([BREATHING_PREFIX, FULL_BRIGHTNESS_PREFIX])
    fixed = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
    execute_led_mode_change(
        _device(),
        expected=LedModeState.BREATHING,
        target=LedModeState.FULL_BRIGHTNESS,
        output_root=tmp_path / "results",
        state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: fixed,
    )
    state = load_led_mode_state(state_path)
    assert len(state["history"]) == 2
    assert state["history"][0]["previous_semantic_mode"] == "Full Brightness"
    assert state["history"][0]["new_semantic_mode"] == "Breathing"
    assert state["history"][0]["power_cycle_persistence"] == "confirmed"
    reverse = state["history"][1]
    assert reverse["previous_prefix"] == "ff 00 00 02 05 01"
    assert reverse["confirmed_new_prefix"] == "ff 00 00 01 00 01"
    assert reverse["previous_semantic_mode"] == "Breathing"
    assert reverse["new_semantic_mode"] == "Full Brightness"
    assert reverse["immediate_read_back_confirmed"] is True
    assert reverse["power_cycle_persistence"] == "pending"
    assert state["current_prefix"] == "ff 00 00 01 00 01"
    assert state["current_semantic_mode"] == "Full Brightness"


def test_off_to_full_transport_preserves_rgb_and_sends_only_exact_reports():
    backend = MockModeIo([TRUE_OFF_PREFIX, CURRENT_FULL_BRIGHTNESS_PREFIX])
    with _transport(backend) as transport:
        result = transport.off_to_full(before_write=lambda plan: None)
    assert result.read_back == CURRENT_FULL_BRIGHTNESS_PREFIX
    assert backend.writes == [
        bytes.fromhex("02 f3 49 04 06 00 00 00 7b 2d d2 01 00 01 00 00"),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert all(report[0] == 2 for report in backend.writes)
    assert all(b"044f" not in report for report in backend.writes)


def test_off_to_full_execute_records_pending_transition(tmp_path: Path):
    state_path = _state(tmp_path / "off-state.json")
    state = load_led_mode_state(state_path)
    state["current_prefix"] = TRUE_OFF_PREFIX.hex(" ")
    state["current_semantic_mode"] = "True Off"
    state["current_observation"] = {
        "prefix": TRUE_OFF_PREFIX.hex(" "),
        "semantic_mode": "True Off",
        "source_of_authority": "controlled-windows-mode-off-post-read",
    }
    atomic_write_led_mode_state(state_path, state)
    backend = MockModeIo([TRUE_OFF_PREFIX, CURRENT_FULL_BRIGHTNESS_PREFIX])
    output = execute_led_mode_change(
        _device(), expected=LedModeState.TRUE_OFF,
        target=LedModeState.CURRENT_FULL_BRIGHTNESS,
        output_root=tmp_path / "results", state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc),
    )
    saved = load_led_mode_state(state_path)
    assert saved["history"][-1]["previous_semantic_mode"] == "True Off"
    assert saved["history"][-1]["new_semantic_mode"] == "Full Brightness"
    assert saved["history"][-1]["power_cycle_persistence"] == "pending"
    assert saved["current_prefix"] == CURRENT_FULL_BRIGHTNESS_PREFIX.hex(" ")
    assert saved["history"][-1]["external_state_discontinuity"] is False
    assert (output / "postwrite_read.json").exists()


def test_pending_led_transition_blocks_before_transport(tmp_path: Path):
    state_path = _state(tmp_path / "led-state.json")
    record_led_mode_transition(
        state_path=state_path,
        previous_prefix=FULL_BRIGHTNESS_PREFIX,
        new_prefix=BREATHING_PREFIX,
        operation_timestamp="2026-07-27T12:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T12:00:01+00:00",
    )
    opened = []
    with pytest.raises(GuardedLedModeError, match="pending persistence"):
        execute_led_mode_change(
            _device(),
            expected=LedModeState.BREATHING,
            target=LedModeState.FULL_BRIGHTNESS,
            state_path=state_path,
            transport_factory=lambda device: opened.append(device),
        )
    assert opened == []


def _breathing_live_read(tmp_path: Path) -> Path:
    destination = tmp_path / "read"
    destination.mkdir()
    for name in ("live_transactions.json", "live_memory_map.json"):
        shutil.copyfile(POST_FULL_READ / name, destination / name)
    path = destination / "live_memory_map.json"
    document = json.loads(path.read_text())
    for offset, value in enumerate(BREATHING_PREFIX):
        document["bytes"][f"0x{0x0449 + offset:04X}"] = value
    path.write_text(json.dumps(document, indent=2) + "\n")
    return destination


def test_offline_persistence_confirmation_checks_all_six_bytes(tmp_path: Path):
    state_path = _state(tmp_path / "led-state.json")
    record_led_mode_transition(
        state_path=state_path,
        previous_prefix=FULL_BRIGHTNESS_PREFIX,
        new_prefix=BREATHING_PREFIX,
        operation_timestamp="2026-07-27T12:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T12:00:01+00:00",
    )
    evidence = _breathing_live_read(tmp_path)
    confirmed = confirm_led_mode_persistence(
        evidence, state_path=state_path,
        clock=lambda: datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc),
    )
    assert confirmed["history"][0]["power_cycle_persistence"] == "confirmed"
    assert confirmed["history"][0][
        "latest_confirming_live_read_directory"
    ] == str(evidence)


def test_invalid_persistence_evidence_is_rejected(tmp_path: Path):
    state_path = _state(tmp_path / "led-state.json")
    record_led_mode_transition(
        state_path=state_path,
        previous_prefix=FULL_BRIGHTNESS_PREFIX,
        new_prefix=BREATHING_PREFIX,
        operation_timestamp="2026-07-27T12:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T12:00:01+00:00",
    )
    with pytest.raises(KnownLedModeStateError, match="does not confirm"):
        confirm_led_mode_persistence(
            POST_FULL_READ, state_path=state_path
        )


def test_dry_run_and_static_audit_prove_fixed_boundary():
    lines = "\n".join(dry_run_lines())
    assert "02 f3 49 04 06 00 00 00 ff 00 00 02 05 01 00 00" in lines
    assert "02 f1 02 04" in lines
    assert "F5 is absent" in lines
    assert "0x044F is excluded" in lines
    results = safety_audit()
    assert len(results) == 8
    assert any("no generic" in item for item in results)


def test_state_commands_are_offline_and_deterministic(tmp_path: Path, capsys):
    state_path = _state(tmp_path / "state.json")
    assert state_main(["--state-file", str(state_path), "validate"]) == 0
    assert "current=Full Brightness" in capsys.readouterr().out
    assert state_main(["--state-file", str(state_path), "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["current_prefix"] == "ff 00 00 01 00 01"
    source = (
        Path(__file__).parents[1] / "src" / "venus_profile"
        / "known_led_mode_state.py"
    ).read_text(encoding="utf-8")
    assert "/dev/hidraw" not in source
    assert "discover_venus_hidraw" not in source
    assert "_hid_iocsfeature" not in source


def test_malformed_led_state_is_not_ignored(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(KnownLedModeStateError, match="could not parse"):
        load_led_mode_state(path)
