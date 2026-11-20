from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from venus_profile.device_session import DeviceSession
from venus_profile.guarded_led_colour import (
    LIGHTING_FINALISATION_REPORT,
    GuardedLedColourError,
    GuardedLedColourTransport,
    LedColour,
    build_led_colour_report,
    dry_run_lines,
    execute_colour_change,
    main,
    safety_audit,
    validate_guarded_colour_report,
)
from venus_profile.known_led_colour_state import (
    LED_COLOUR_STATE_PATH,
    OFFICIAL_BLUE_PREFIX,
    OFFICIAL_GREEN_PREFIX,
    OFFICIAL_RED_PREFIX,
    KnownLedColourStateError,
    atomic_write_colour_state,
    confirm_colour_persistence,
    initial_state,
    load_colour_state,
    main as state_main,
    record_colour_transition,
)
from venus_profile.readonly_hidraw import (
    HidrawDevice, HidrawPermissionError, ReadOnlyHidrawTransport,
    validate_f2_request,
)
from evidence_paths import EVIDENCE  # noqa: E402


BLUE_READ = (
    EVIDENCE / "usb-captures" / "live-reads"
    / "venus_read_20260727T112130.097148Z"
)


def _device(interface: int = 2) -> HidrawDevice:
    return HidrawDevice(
        Path("/mock/hidraw4"), Path("/mock/sysfs"), 0x04D9, 0xFC55,
        interface, 0xFFA0, 1,
    )


def _response(prefix: bytes) -> bytes:
    return bytes.fromhex("02 08 48 64 06 00 fa fa") + prefix + b"\x00\x00"


class MockColourIo:
    def __init__(self, prefixes, *, fail_f3=False, fail_f1=False):
        self.responses = [_response(item) for item in prefixes]
        self.fail_f3, self.fail_f1 = fail_f3, fail_f1
        self.f2, self.writes = [], []
        self.closed = False

    def send_f2(self, request):
        validate_f2_request(request)
        self.f2.append(request)

    def get_feature(self, report_id, length):
        assert (report_id, length) == (2, 16)
        return self.responses.pop(0)

    def send_colour(self, colour):
        if self.fail_f3:
            raise GuardedLedColourError("mock F3 failure")
        report = build_led_colour_report(colour)
        self.writes.append(report)
        return report

    def finalise_lighting(self):
        if self.fail_f1:
            raise GuardedLedColourError("mock F1 failure")
        self.writes.append(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT

    def close(self):
        self.closed = True


def _transport(backend):
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: backend
        ),
    )
    return GuardedLedColourTransport(
        session, backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )


def _state(path):
    atomic_write_colour_state(
        path, initial_state("2026-07-27T11:21:30+00:00")
    )
    return path


def _confirmed_red_state(path):
    _state(path)
    state = record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_BLUE_PREFIX,
        new_prefix=OFFICIAL_RED_PREFIX,
        operation_timestamp="2026-07-27T11:37:58+00:00",
        result_directory=path.parent / "blue-to-red",
        updated_at="2026-07-27T11:37:59+00:00",
    )
    state["history"][0]["power_cycle_persistence"] = "confirmed"
    state["history"][0]["persistence_confirmed_at"] = (
        "2026-07-27T11:39:02+00:00"
    )
    state["history"][0]["latest_confirming_live_read_directory"] = (
        "/saved/red/live-read"
    )
    atomic_write_colour_state(path, state)
    return path


def _confirmed_green_state(path):
    _confirmed_red_state(path)
    state = record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_RED_PREFIX,
        new_prefix=OFFICIAL_BLUE_PREFIX,
        operation_timestamp="2026-07-27T11:43:48+00:00",
        result_directory=path.parent / "red-to-blue",
        updated_at="2026-07-27T11:43:49+00:00",
    )
    state["history"][-1]["power_cycle_persistence"] = "confirmed"
    state["history"][-1]["persistence_confirmed_at"] = (
        "2026-07-27T11:44:48+00:00"
    )
    state["history"][-1]["latest_confirming_live_read_directory"] = (
        "/saved/blue/live-read"
    )
    atomic_write_colour_state(path, state)
    state = record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_BLUE_PREFIX,
        new_prefix=OFFICIAL_GREEN_PREFIX,
        operation_timestamp="2026-07-27T11:49:19+00:00",
        result_directory=path.parent / "blue-to-green-windows",
        updated_at="2026-07-27T11:50:52+00:00",
    )
    state["history"][-1]["power_cycle_persistence"] = "confirmed"
    state["history"][-1]["persistence_confirmed_at"] = (
        "2026-07-27T11:50:52+00:00"
    )
    state["history"][-1]["latest_confirming_live_read_directory"] = (
        "/saved/green/live-read"
    )
    atomic_write_colour_state(path, state)
    return path


def test_exact_colours_prefixes_suffix_and_f3():
    assert LedColour.OFFICIAL_BLUE.rgb == (14, 15, 255)
    assert LedColour.OFFICIAL_RED.rgb == (255, 0, 0)
    assert LedColour.OFFICIAL_BLUE.value == bytes.fromhex(
        "0e 0f ff 01 00 01"
    )
    assert LedColour.OFFICIAL_RED.value == bytes.fromhex(
        "ff 00 00 01 00 01"
    )
    assert LedColour.OFFICIAL_GREEN.value == bytes.fromhex(
        "00 ff 01 01 00 01"
    )
    assert LedColour.OFFICIAL_GREEN.rgb == (0, 255, 1)
    assert build_led_colour_report(LedColour.OFFICIAL_RED) == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00"
    )
    for colour in LedColour:
        assert colour.value[3:] == bytes.fromhex("01 00 01")


@pytest.mark.parametrize("index", range(16))
def test_every_other_report_byte_is_rejected(index):
    report = bytearray(build_led_colour_report(LedColour.OFFICIAL_RED))
    report[index] ^= 0x80
    with pytest.raises(GuardedLedColourError):
        validate_guarded_colour_report(bytes(report))


@pytest.mark.parametrize(
    "value",
    [b"\x01\x02\x03", (1, 2, 3), "#123456", 0x123456],
)
def test_arbitrary_rgb_is_rejected(value):
    with pytest.raises(GuardedLedColourError):
        build_led_colour_report(value)  # type: ignore[arg-type]


def test_transient_dark_green_is_not_a_supported_value_or_report():
    transient = bytes.fromhex("02 b5 34 01 00 01")
    assert transient not in {colour.value for colour in LedColour}
    report = bytes.fromhex("02 f3 49 04 06 00 00 00") + transient + b"\0\0"
    with pytest.raises(GuardedLedColourError):
        validate_guarded_colour_report(report)


def test_only_exact_f1_is_accepted():
    assert validate_guarded_colour_report(
        LIGHTING_FINALISATION_REPORT
    ) == "lighting-finalisation"
    bad = bytearray(LIGHTING_FINALISATION_REPORT)
    bad[3] ^= 1
    with pytest.raises(GuardedLedColourError):
        validate_guarded_colour_report(bytes(bad))


def test_blue_to_red_success_and_exact_readback():
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, OFFICIAL_RED_PREFIX])
    with _transport(backend) as transport:
        result = transport.blue_to_red(before_write=lambda plan: None)
    assert result.read_back == OFFICIAL_RED_PREFIX
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_RED),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert len(backend.f2) == 2


def test_reverse_red_to_blue_is_constructible_offline():
    backend = MockColourIo([OFFICIAL_RED_PREFIX, OFFICIAL_BLUE_PREFIX])
    with _transport(backend) as transport:
        result = transport.red_to_blue(before_write=lambda plan: None)
    assert result.read_back == OFFICIAL_BLUE_PREFIX
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_BLUE),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_reverse_rejects_blue_as_already_changed():
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX])
    with pytest.raises(GuardedLedColourError, match="already Official Blue"):
        with _transport(backend) as transport:
            transport.red_to_blue(before_write=lambda plan: None)
    assert backend.writes == []


def test_reverse_rejects_every_other_source_prefix():
    backend = MockColourIo([bytes.fromhex("ff 00 01 01 00 01")])
    with pytest.raises(GuardedLedColourError, match="unexpected lighting"):
        with _transport(backend) as transport:
            transport.red_to_blue(before_write=lambda plan: None)
    assert backend.writes == []


def test_reverse_failed_readback_has_no_retry_or_restoration():
    backend = MockColourIo([OFFICIAL_RED_PREFIX, OFFICIAL_RED_PREFIX])
    with pytest.raises(GuardedLedColourError, match="read-back mismatch"):
        with _transport(backend) as transport:
            transport.red_to_blue(before_write=lambda plan: None)
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_BLUE),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_green_to_blue_exact_success():
    backend = MockColourIo([OFFICIAL_GREEN_PREFIX, OFFICIAL_BLUE_PREFIX])
    with _transport(backend) as transport:
        result = transport.green_to_blue(before_write=lambda plan: None)
    assert result.read_back == OFFICIAL_BLUE_PREFIX
    assert backend.writes == [
        bytes.fromhex(
            "02 f3 49 04 06 00 00 00 0e 0f ff 01 00 01 00 00"
        ),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_blue_to_green_exact_success():
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, OFFICIAL_GREEN_PREFIX])
    with _transport(backend) as transport:
        result = transport.blue_to_green(before_write=lambda plan: None)
    assert result.read_back == OFFICIAL_GREEN_PREFIX
    assert backend.writes == [
        bytes.fromhex(
            "02 f3 49 04 06 00 00 00 00 ff 01 01 00 01 00 00"
        ),
        LIGHTING_FINALISATION_REPORT,
    ]


@pytest.mark.parametrize(
    "source",
    [
        OFFICIAL_RED_PREFIX,
        bytes.fromhex("02 b5 34 01 00 01"),
        bytes.fromhex("0e 0f ff 02 00 01"),
        bytes.fromhex("0e 0f fe 01 00 01"),
    ],
)
def test_blue_to_green_rejects_red_transient_and_other_sources(source):
    backend = MockColourIo([source])
    with pytest.raises(GuardedLedColourError):
        with _transport(backend) as transport:
            transport.blue_to_green(before_write=lambda plan: None)
    assert backend.writes == []


def test_blue_to_green_rejects_green_as_already_changed():
    backend = MockColourIo([OFFICIAL_GREEN_PREFIX])
    with pytest.raises(GuardedLedColourError, match="already Official Green"):
        with _transport(backend) as transport:
            transport.blue_to_green(before_write=lambda plan: None)
    assert backend.writes == []


def test_blue_to_green_failed_readback_has_no_retry_or_restore():
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, OFFICIAL_BLUE_PREFIX])
    with pytest.raises(GuardedLedColourError, match="read-back mismatch"):
        with _transport(backend) as transport:
            transport.blue_to_green(before_write=lambda plan: None)
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_GREEN),
        LIGHTING_FINALISATION_REPORT,
    ]


@pytest.mark.parametrize(
    "source",
    [
        bytes.fromhex("02 b5 34 01 00 01"),
        OFFICIAL_RED_PREFIX,
        bytes.fromhex("00 ff 01 02 00 01"),
        bytes.fromhex("00 fe 01 01 00 01"),
    ],
)
def test_green_direction_rejects_transient_red_and_other_sources(source):
    backend = MockColourIo([source])
    with pytest.raises(GuardedLedColourError):
        with _transport(backend) as transport:
            transport.green_to_blue(before_write=lambda plan: None)
    assert backend.writes == []


def test_green_direction_rejects_blue_as_already_changed():
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX])
    with pytest.raises(GuardedLedColourError, match="already Official Blue"):
        with _transport(backend) as transport:
            transport.green_to_blue(before_write=lambda plan: None)
    assert backend.writes == []


def test_green_readback_mismatch_has_no_retry_or_restore():
    backend = MockColourIo([OFFICIAL_GREEN_PREFIX, OFFICIAL_GREEN_PREFIX])
    with pytest.raises(GuardedLedColourError, match="read-back mismatch"):
        with _transport(backend) as transport:
            transport.green_to_blue(before_write=lambda plan: None)
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_BLUE),
        LIGHTING_FINALISATION_REPORT,
    ]


@pytest.mark.parametrize(
    ("f3", "f1", "message", "writes"),
    [(True, False, "F3 failure", 0), (False, True, "F1 failure", 1)],
)
def test_green_f3_and_f1_fail_closed(f3, f1, message, writes):
    backend = MockColourIo(
        [OFFICIAL_GREEN_PREFIX], fail_f3=f3, fail_f1=f1
    )
    with pytest.raises(GuardedLedColourError, match=message):
        with _transport(backend) as transport:
            transport.green_to_blue(before_write=lambda plan: None)
    assert len(backend.writes) == writes


@pytest.mark.parametrize(
    "source",
    [
        OFFICIAL_RED_PREFIX,
        bytes.fromhex("0e 0f ff 02 00 01"),
        bytes.fromhex("0e 0f fe 01 00 01"),
        b"\x00" * 6,
    ],
)
def test_every_other_source_prefix_rejects_before_write(source):
    backend = MockColourIo([source])
    with pytest.raises(GuardedLedColourError):
        with _transport(backend) as transport:
            transport.blue_to_red(before_write=lambda plan: None)
    assert backend.writes == []


def test_mismatch_does_not_open_private_writer():
    reader = MockColourIo([bytes.fromhex("0e 0f ff 02 00 01")])
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: reader
        ),
    )
    opened = []
    transport = GuardedLedColourTransport(
        session, backend_factory=lambda path: opened.append(path),
        sleeper=lambda seconds: None,
    )
    with pytest.raises(GuardedLedColourError):
        with transport:
            transport.blue_to_red(before_write=lambda plan: None)
    assert opened == []


@pytest.mark.parametrize(
    ("f3", "f1", "message", "writes"),
    [(True, False, "F3 failure", 0), (False, True, "F1 failure", 1)],
)
def test_f3_and_f1_fail_closed(f3, f1, message, writes):
    backend = MockColourIo(
        [OFFICIAL_BLUE_PREFIX], fail_f3=f3, fail_f1=f1
    )
    with pytest.raises(GuardedLedColourError, match=message):
        with _transport(backend) as transport:
            transport.blue_to_red(before_write=lambda plan: None)
    assert len(backend.writes) == writes


@pytest.mark.parametrize("index", range(6))
def test_any_readback_mismatch_has_no_retry_or_restore(index):
    wrong = bytearray(OFFICIAL_RED_PREFIX)
    wrong[index] ^= 1
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, bytes(wrong)])
    with pytest.raises(GuardedLedColourError):
        with _transport(backend) as transport:
            transport.blue_to_red(before_write=lambda plan: None)
    assert backend.writes == [
        build_led_colour_report(LedColour.OFFICIAL_RED),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_default_and_dry_run_are_offline(capsys):
    no_discovery = lambda: pytest.fail("must not discover")
    assert main([], discovery=no_discovery) == 0
    assert main(
        ["--dry-run", "--blue-to-red"], discovery=no_discovery
    ) == 0


def test_permission_failure_precedes_confirmation():
    prompts = []
    with pytest.raises(HidrawPermissionError):
        main(
            [
                "--live-led-colour-write", "--blue-to-red",
                "--acknowledge-device-write",
            ],
            discovery=_device, permission_check=lambda path: False,
            input_fn=lambda prompt: prompts.append(prompt) or "",
        )
    assert prompts == []


def test_confirmation_is_exact_and_mismatch_does_not_execute(tmp_path):
    calls = []
    assert main(
        [
            "--live-led-colour-write", "--blue-to-red",
            "--acknowledge-device-write",
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 BLUE TO RED ",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []
    prompts = []
    assert main(
        [
            "--live-led-colour-write", "--blue-to-red",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: (
            prompts.append(prompt) or "WRITE 0449 BLUE TO RED"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert prompts == ["Type exactly WRITE 0449 BLUE TO RED: "]


def test_reverse_confirmation_is_exact(tmp_path):
    prompts = []
    assert main(
        [
            "--live-led-colour-write", "--red-to-blue",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: (
            prompts.append(prompt) or "WRITE 0449 RED TO BLUE"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert prompts == ["Type exactly WRITE 0449 RED TO BLUE: "]
    calls = []
    assert main(
        [
            "--live-led-colour-write", "--red-to-blue",
            "--acknowledge-device-write",
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 BLUE TO RED",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []


def test_green_confirmation_is_exact(tmp_path):
    prompts = []
    assert main(
        [
            "--live-led-colour-write", "--green-to-blue",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: (
            prompts.append(prompt) or "WRITE 0449 GREEN TO BLUE"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert prompts == ["Type exactly WRITE 0449 GREEN TO BLUE: "]
    calls = []
    assert main(
        [
            "--live-led-colour-write", "--green-to-blue",
            "--acknowledge-device-write",
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 BLUE TO GREEN",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []


def test_blue_to_green_confirmation_is_exact(tmp_path):
    prompts = []
    assert main(
        [
            "--live-led-colour-write", "--blue-to-green",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: (
            prompts.append(prompt) or "WRITE 0449 BLUE TO GREEN"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert prompts == ["Type exactly WRITE 0449 BLUE TO GREEN: "]
    calls = []
    assert main(
        [
            "--live-led-colour-write", "--blue-to-green",
            "--acknowledge-device-write",
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 GREEN TO BLUE",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []


def test_success_artifacts_pending_and_state_namespace_separation(tmp_path):
    state_path = _state(tmp_path / "colour.json")
    other_paths = [
        EVIDENCE / "usb-captures" / "device-state"
        / "known_device_state.json",
        EVIDENCE / "usb-captures" / "device-state"
        / "known_led_mode_state.json",
    ]
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in other_paths}
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, OFFICIAL_RED_PREFIX])
    fixed = datetime(2026, 7, 27, 13, 0, tzinfo=timezone.utc)
    output = execute_colour_change(
        _device(), expected=LedColour.OFFICIAL_BLUE,
        target=LedColour.OFFICIAL_RED,
        output_root=tmp_path / "results", state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: fixed,
    )
    assert {item.name for item in output.iterdir()} == {
        "operation.json", "reports_sent.tsv", "prewrite_read.json",
        "postwrite_read.json", "result.md",
    }
    operation = json.loads((output / "operation.json").read_text())
    assert operation["target_prefix"] == "ff 00 00 01 00 01"
    assert operation["immediate_read_back_confirmed"] is True
    assert operation["power_cycle_persistence"] == "pending"
    state = load_colour_state(state_path)
    assert state["current_semantic_colour"] == "Official Red"
    assert state["current_rgb"] == [255, 0, 0]
    assert state["history"][0]["previous_rgb"] == [14, 15, 255]
    assert state["history"][0]["new_rgb"] == [255, 0, 0]
    assert state["history"][0]["power_cycle_persistence"] == "pending"
    for path, digest in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_reverse_preserves_confirmed_forward_history_and_sets_pending(tmp_path):
    path = _confirmed_red_state(tmp_path / "colour.json")
    backend = MockColourIo([OFFICIAL_RED_PREFIX, OFFICIAL_BLUE_PREFIX])
    execute_colour_change(
        _device(), expected=LedColour.OFFICIAL_RED,
        target=LedColour.OFFICIAL_BLUE,
        output_root=tmp_path / "results", state_path=path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc),
    )
    state = load_colour_state(path)
    assert len(state["history"]) == 2
    forward, reverse = state["history"]
    assert forward["previous_semantic_colour"] == "Official Blue"
    assert forward["new_semantic_colour"] == "Official Red"
    assert forward["power_cycle_persistence"] == "confirmed"
    assert reverse["previous_prefix"] == "ff 00 00 01 00 01"
    assert reverse["confirmed_new_prefix"] == "0e 0f ff 01 00 01"
    assert reverse["previous_rgb"] == [255, 0, 0]
    assert reverse["new_rgb"] == [14, 15, 255]
    assert reverse["immediate_read_back_confirmed"] is True
    assert reverse["power_cycle_persistence"] == "pending"
    assert state["current_prefix"] == "0e 0f ff 01 00 01"
    assert state["current_semantic_colour"] == "Official Blue"
    assert state["current_rgb"] == [14, 15, 255]


def test_green_to_blue_preserves_red_blue_history_and_sets_pending(tmp_path):
    path = _confirmed_green_state(tmp_path / "colour.json")
    backend = MockColourIo([OFFICIAL_GREEN_PREFIX, OFFICIAL_BLUE_PREFIX])
    execute_colour_change(
        _device(), expected=LedColour.OFFICIAL_GREEN,
        target=LedColour.OFFICIAL_BLUE,
        output_root=tmp_path / "results", state_path=path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc),
    )
    state = load_colour_state(path)
    assert len(state["history"]) == 4
    assert [
        item["new_semantic_colour"] for item in state["history"][:3]
    ] == ["Official Red", "Official Blue", "Official Green"]
    assert all(
        item["power_cycle_persistence"] == "confirmed"
        for item in state["history"][:3]
    )
    transition = state["history"][-1]
    assert transition["previous_prefix"] == "00 ff 01 01 00 01"
    assert transition["confirmed_new_prefix"] == "0e 0f ff 01 00 01"
    assert transition["previous_rgb"] == [0, 255, 1]
    assert transition["new_rgb"] == [14, 15, 255]
    assert transition["immediate_read_back_confirmed"] is True
    assert transition["power_cycle_persistence"] == "pending"
    assert state["current_prefix"] == "0e 0f ff 01 00 01"
    assert state["current_semantic_colour"] == "Official Blue"
    assert state["current_rgb"] == [14, 15, 255]


def test_blue_to_green_preserves_complete_history_and_sets_pending(tmp_path):
    source = LED_COLOUR_STATE_PATH
    path = tmp_path / "colour.json"
    path.write_bytes(source.read_bytes())
    before = load_colour_state(path)
    if before["current_prefix"] == "00 ff 01 01 00 01":
        before["history"] = before["history"][:-1]
        prior = before["history"][-1]
        before["current_prefix"] = prior["confirmed_new_prefix"]
        before["current_semantic_colour"] = prior["new_semantic_colour"]
        before["current_rgb"] = prior["new_rgb"]
        before["updated_at"] = prior["persistence_confirmed_at"]
        atomic_write_colour_state(path, before)
        before = load_colour_state(path)
    assert before["current_prefix"] == "0e 0f ff 01 00 01"
    assert all(
        item["power_cycle_persistence"] == "confirmed"
        for item in before["history"]
    )
    original_history = json.loads(json.dumps(before["history"]))
    backend = MockColourIo([OFFICIAL_BLUE_PREFIX, OFFICIAL_GREEN_PREFIX])
    execute_colour_change(
        _device(), expected=LedColour.OFFICIAL_BLUE,
        target=LedColour.OFFICIAL_GREEN,
        output_root=tmp_path / "results", state_path=path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
    )
    state = load_colour_state(path)
    assert state["history"][:-1] == original_history
    transition = state["history"][-1]
    assert transition["previous_prefix"] == "0e 0f ff 01 00 01"
    assert transition["confirmed_new_prefix"] == "00 ff 01 01 00 01"
    assert transition["previous_rgb"] == [14, 15, 255]
    assert transition["new_rgb"] == [0, 255, 1]
    assert transition["immediate_read_back_confirmed"] is True
    assert transition["power_cycle_persistence"] == "pending"
    assert state["current_prefix"] == "00 ff 01 01 00 01"
    assert state["current_semantic_colour"] == "Official Green"
    assert state["current_rgb"] == [0, 255, 1]


def test_pending_colour_blocks_only_next_colour_transport(tmp_path):
    path = _state(tmp_path / "colour.json")
    record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_BLUE_PREFIX,
        new_prefix=OFFICIAL_RED_PREFIX,
        operation_timestamp="2026-07-27T13:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T13:00:01+00:00",
    )
    opened = []
    with pytest.raises(GuardedLedColourError, match="pending persistence"):
        execute_colour_change(
            _device(), expected=LedColour.OFFICIAL_RED,
            target=LedColour.OFFICIAL_BLUE, state_path=path,
            transport_factory=lambda device: opened.append(device),
        )
    assert opened == []


def _red_read(tmp_path):
    destination = tmp_path / "read"
    destination.mkdir()
    for name in ("live_transactions.json", "live_memory_map.json"):
        shutil.copyfile(BLUE_READ / name, destination / name)
    path = destination / "live_memory_map.json"
    document = json.loads(path.read_text())
    for offset, value in enumerate(OFFICIAL_RED_PREFIX):
        document["bytes"][f"0x{0x0449 + offset:04X}"] = value
    path.write_text(json.dumps(document, indent=2) + "\n")
    return destination


def test_offline_persistence_confirmation_checks_full_prefix(tmp_path):
    path = _state(tmp_path / "colour.json")
    record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_BLUE_PREFIX,
        new_prefix=OFFICIAL_RED_PREFIX,
        operation_timestamp="2026-07-27T13:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T13:00:01+00:00",
    )
    evidence = _red_read(tmp_path)
    state = confirm_colour_persistence(
        evidence, state_path=path,
        clock=lambda: datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc),
    )
    assert state["history"][0]["power_cycle_persistence"] == "confirmed"
    assert state["history"][0][
        "latest_confirming_live_read_directory"
    ] == str(evidence)


def test_invalid_persistence_evidence_rejected(tmp_path):
    path = _state(tmp_path / "colour.json")
    record_colour_transition(
        state_path=path, previous_prefix=OFFICIAL_BLUE_PREFIX,
        new_prefix=OFFICIAL_RED_PREFIX,
        operation_timestamp="2026-07-27T13:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T13:00:01+00:00",
    )
    with pytest.raises(KnownLedColourStateError, match="does not confirm"):
        confirm_colour_persistence(BLUE_READ, state_path=path)


def test_state_commands_are_offline(tmp_path, capsys):
    path = _state(tmp_path / "colour.json")
    assert state_main(["--state-file", str(path), "validate"]) == 0
    assert "current=Official Blue" in capsys.readouterr().out
    assert state_main(["--state-file", str(path), "show"]) == 0
    assert json.loads(capsys.readouterr().out)["history"] == []
    source = (
        Path(__file__).parents[1] / "src" / "venus_profile"
        / "known_led_colour_state.py"
    ).read_text()
    assert "/dev/hidraw" not in source
    assert "discover_venus_hidraw" not in source
    assert "_hid_iocsfeature" not in source


def test_project_seed_state_and_static_safety():
    state = load_colour_state(LED_COLOUR_STATE_PATH)
    assert state["current_prefix"] == "00 ff 01 01 00 01"
    assert state["current_semantic_colour"] == "Official Green"
    assert state["current_rgb"] == [0, 255, 1]
    assert state["history"][-1]["power_cycle_persistence"] == "confirmed"
    assert state["history"][-1]["source_of_authority"] == (
        "guarded-linux-led-colour-write"
    )
    assert state["history"][-1]["latest_confirming_live_read_directory"].endswith(
        "venus_read_20260727T121614.206708Z"
    )
    lines = "\n".join(dry_run_lines())
    assert "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00" in lines
    assert "F5 absent" in lines
    results = safety_audit()
    assert len(results) == 8
    assert any("arbitrary RGB" in item for item in results)
    assert any("02 B5 34" in item for item in results)
