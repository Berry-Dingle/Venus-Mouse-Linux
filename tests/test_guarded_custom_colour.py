from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from venus_profile.device_session import DeviceSession
from venus_profile.guarded_custom_colour import (
    INITIAL_SEMANTIC_HEX, LIGHTING_FINALISATION_REPORT, LIVE_TARGET_HEX,
    CustomColour, GuardedCustomColourError, GuardedCustomColourTransport,
    build_custom_colour_report, dry_run_lines, execute_custom_colour_change,
    main, safety_audit, validate_live_custom_report,
)
from venus_profile.known_custom_colour_state import (
    CUSTOM_COLOUR_STATE_PATH, INITIAL_LIVE_READ,
    KnownCustomColourStateError, atomic_write_custom_colour_state,
    confirm_custom_colour_persistence, initial_state,
    load_custom_colour_state, main as state_main,
    normalise_semantic_hex, record_custom_colour_transition,
    semantic_hex_to_device_rgb, semantic_hex_to_prefix,
    semantic_hex_to_rgb,
)
from venus_profile.known_device_state import _load_live_read
from venus_profile.readonly_hidraw import (
    HidrawDevice, HidrawPermissionError, ReadOnlyHidrawTransport,
    validate_f2_request,
)
from evidence_paths import EVIDENCE  # noqa: E402


SOURCE_PREFIX = bytes.fromhex("00 00 ff 01 00 01")
TARGET_PREFIX = bytes.fromhex("ff 00 00 01 00 01")


def _device(interface: int = 2) -> HidrawDevice:
    return HidrawDevice(
        Path("/mock/hidraw4"), Path("/mock/sysfs"), 0x04D9, 0xFC55,
        interface, 0xFFA0, 1,
    )


def _response(prefix: bytes) -> bytes:
    return bytes.fromhex("02 08 48 64 06 00 fa fa") + prefix + b"\0\0"


class MockCustomIo:
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

    def send_custom_target(self):
        if self.fail_f3:
            raise GuardedCustomColourError("mock F3 failure")
        report = build_custom_colour_report(LIVE_TARGET_HEX)
        self.writes.append(report)
        return report

    def finalise_lighting(self):
        if self.fail_f1:
            raise GuardedCustomColourError("mock F1 failure")
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
    return GuardedCustomColourTransport(
        session, backend_factory=lambda path: backend,
        sleeper=lambda seconds: None,
    )


def _state(path):
    atomic_write_custom_colour_state(
        path, initial_state("2026-07-27T12:44:28+00:00")
    )
    return path


@pytest.mark.parametrize(
    ("value", "normalised", "rgb", "bgr"),
    [
        ("#7B2DD2", "#7B2DD2", (123, 45, 210), "7b 2d d2"),
        ("#12a7e4", "#12A7E4", (18, 167, 228), "12 a7 e4"),
        ("#FF0000", "#FF0000", (255, 0, 0), "ff 00 00"),
        ("#00FF00", "#00FF00", (0, 255, 0), "00 ff 00"),
        ("#0000FF", "#0000FF", (0, 0, 255), "00 00 ff"),
    ],
)
def test_strict_hex_parse_rgb_and_bgr(value, normalised, rgb, bgr):
    colour = CustomColour.parse(value)
    assert normalise_semantic_hex(value) == normalised
    assert semantic_hex_to_rgb(value) == rgb
    assert semantic_hex_to_device_rgb(value) == bytes.fromhex(bgr)
    assert colour.prefix == bytes.fromhex(bgr) + bytes.fromhex("01 00 01")


@pytest.mark.parametrize(
    "value",
    [
        "FF0000", "#F00", "#FFFF0000", "red", "255,0,0", "0xFF0000",
        "#GG0000", "#FF 00 00", " #FF0000", "#FF0000 ", "", None,
    ],
)
def test_malformed_shorthand_alpha_named_raw_and_whitespace_rejected(value):
    with pytest.raises(GuardedCustomColourError):
        CustomColour.parse(value)  # type: ignore[arg-type]


def test_exact_source_target_prefix_and_f3():
    assert INITIAL_SEMANTIC_HEX == "#0000FF"
    assert LIVE_TARGET_HEX == "#FF0000"
    assert semantic_hex_to_prefix(INITIAL_SEMANTIC_HEX) == SOURCE_PREFIX
    assert semantic_hex_to_prefix(LIVE_TARGET_HEX) == TARGET_PREFIX
    report = build_custom_colour_report("#FF0000")
    assert report == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00"
    )
    assert validate_live_custom_report(report) == (
        "profile-1-custom-colour-boundary-red"
    )
    assert report[11:14] == bytes.fromhex("01 00 01")
    assert len(report) == 16


def test_native_rgb_is_never_reversed_regression():
    assert semantic_hex_to_device_rgb("#FF0000") == bytes.fromhex("ff 00 00")
    assert semantic_hex_to_device_rgb("#0000FF") == bytes.fromhex("00 00 ff")
    assert semantic_hex_to_device_rgb("#7B2DD2") != bytes.fromhex("d2 2d 7b")
    assert semantic_hex_to_device_rgb("#12A7E4") != bytes.fromhex("e4 a7 12")


@pytest.mark.parametrize("index", range(16))
def test_every_mutated_live_f3_byte_rejected(index):
    report = bytearray(build_custom_colour_report("#FF0000"))
    report[index] ^= 0x80
    with pytest.raises(GuardedCustomColourError):
        validate_live_custom_report(bytes(report))


def test_other_valid_offline_colour_report_not_live_submittable():
    assert build_custom_colour_report("#123456") == bytes.fromhex(
        "02 f3 49 04 06 00 00 00 12 34 56 01 00 01 00 00"
    )
    with pytest.raises(GuardedCustomColourError):
        validate_live_custom_report(build_custom_colour_report("#123456"))


def test_transport_exact_success():
    backend = MockCustomIo([SOURCE_PREFIX, TARGET_PREFIX])
    with _transport(backend) as transport:
        result = transport.source_to_boundary_red(
            before_write=lambda plan: None
        )
    assert result.read_back == TARGET_PREFIX
    assert backend.writes == [
        build_custom_colour_report("#FF0000"),
        LIGHTING_FINALISATION_REPORT,
    ]
    assert len(backend.f2) == 2


@pytest.mark.parametrize(
    "source",
    [
        TARGET_PREFIX,
        bytes.fromhex("e4 a7 12 02 00 01"),
        bytes.fromhex("e4 a7 13 01 00 01"),
        bytes.fromhex("ff 00 00 01 00 01"),
    ],
)
def test_source_mismatch_rejected_before_private_writer(source):
    reader = MockCustomIo([source])
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: reader
        ),
    )
    opened = []
    transport = GuardedCustomColourTransport(
        session, backend_factory=lambda path: opened.append(path),
        sleeper=lambda seconds: None,
    )
    with pytest.raises(GuardedCustomColourError):
        with transport:
            transport.source_to_boundary_red(before_write=lambda plan: None)
    assert opened == []
    assert reader.writes == []


@pytest.mark.parametrize(
    ("f3", "f1", "message", "writes"),
    [(True, False, "F3 failure", 0), (False, True, "F1 failure", 1)],
)
def test_f3_and_f1_failure_no_retry(f3, f1, message, writes):
    backend = MockCustomIo([SOURCE_PREFIX], fail_f3=f3, fail_f1=f1)
    with pytest.raises(GuardedCustomColourError, match=message):
        with _transport(backend) as transport:
            transport.source_to_boundary_red(before_write=lambda plan: None)
    assert len(backend.writes) == writes


@pytest.mark.parametrize("index", range(6))
def test_any_readback_mismatch_has_no_retry_or_restoration(index):
    wrong = bytearray(TARGET_PREFIX)
    wrong[index] ^= 1
    backend = MockCustomIo([SOURCE_PREFIX, bytes(wrong)])
    with pytest.raises(GuardedCustomColourError, match="read-back mismatch"):
        with _transport(backend) as transport:
            transport.source_to_boundary_red(before_write=lambda plan: None)
    assert backend.writes == [
        build_custom_colour_report("#FF0000"),
        LIGHTING_FINALISATION_REPORT,
    ]


def test_live_cli_only_accepts_boundary_red_and_exact_confirmation(tmp_path):
    no_discovery = lambda: pytest.fail("must reject before discovery")
    for value in ("#7B2DD2", "#12A7E4", "#00FF00", "#0000FF", "#ffffff"):
        assert main(
            [
                "--live-custom-colour-write", "--hex", value,
                "--acknowledge-device-write",
            ],
            discovery=no_discovery,
        ) == 2
    prompts = []
    assert main(
        [
            "--live-custom-colour-write", "--hex", "#ff0000",
            "--acknowledge-device-write",
            "--output-root", str(tmp_path / "out"),
            "--state-file", str(tmp_path / "state.json"),
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: (
            prompts.append(prompt) or "WRITE 0449 #0000FF TO #FF0000"
        ),
        executor=lambda *args, **kwargs: tmp_path / "result",
    ) == 0
    assert prompts == [
        "Type exactly WRITE 0449 #0000FF TO #FF0000: "
    ]
    calls = []
    assert main(
        [
            "--live-custom-colour-write", "--hex", "#FF0000",
            "--acknowledge-device-write",
        ],
        discovery=_device, permission_check=lambda path: True,
        input_fn=lambda prompt: "WRITE 0449 #0000FF TO #FF0000 ",
        executor=lambda *args, **kwargs: calls.append(True),
    ) == 2
    assert calls == []


def test_permission_failure_precedes_confirmation():
    prompts = []
    with pytest.raises(HidrawPermissionError):
        main(
            [
                "--live-custom-colour-write", "--hex", "#FF0000",
                "--acknowledge-device-write",
            ],
            discovery=_device, permission_check=lambda path: False,
            input_fn=lambda prompt: prompts.append(prompt) or "",
        )
    assert prompts == []


def test_default_dry_run_audit_and_state_commands_are_offline(tmp_path, capsys):
    no_discovery = lambda: pytest.fail("offline command discovered device")
    assert main([], discovery=no_discovery) == 0
    assert main(
        ["--dry-run", "--hex", "#FF0000"], discovery=no_discovery
    ) == 0
    output = capsys.readouterr().out
    assert "ff 00 00 01 00 01" in output.lower()
    assert "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00" in output
    assert main(["--safety-audit"], discovery=no_discovery) == 0
    path = _state(tmp_path / "state.json")
    assert state_main(["--state-file", str(path), "validate"]) == 0
    assert state_main(["--state-file", str(path), "show"]) == 0


def test_success_artifacts_state_pending_and_namespace_separation(tmp_path):
    state_path = _state(tmp_path / "custom.json")
    other_paths = [
        EVIDENCE / "usb-captures" / "device-state"
        / name for name in (
            "known_device_state.json", "known_led_mode_state.json",
            "known_led_colour_state.json",
        )
    ]
    hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in other_paths
    }
    backend = MockCustomIo([SOURCE_PREFIX, TARGET_PREFIX])
    fixed = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
    output = execute_custom_colour_change(
        _device(), target_hex="#ff0000",
        output_root=tmp_path / "results", state_path=state_path,
        transport_factory=lambda device: _transport(backend),
        clock=lambda: fixed,
    )
    assert {item.name for item in output.iterdir()} == {
        "operation.json", "reports_sent.tsv", "prewrite_read.json",
        "postwrite_read.json", "result.md",
    }
    operation = json.loads((output / "operation.json").read_text())
    assert operation["target_semantic_hex"] == "#FF0000"
    assert operation["target_device_rgb"] == "ff 00 00"
    assert operation["target_prefix"] == "ff 00 00 01 00 01"
    assert operation["immediate_read_back_confirmed"] is True
    assert operation["power_cycle_persistence"] == "pending"
    state = load_custom_colour_state(state_path)
    assert state["current_semantic_hex"] == "#FF0000"
    assert state["current_semantic_rgb"] == [255, 0, 0]
    assert state["current_device_rgb"] == "ff 00 00"
    transition = state["history"][0]
    assert transition["previous_semantic_hex"] == "#0000FF"
    assert transition["new_semantic_hex"] == "#FF0000"
    assert transition["previous_device_rgb"] == "00 00 ff"
    assert transition["new_device_rgb"] == "ff 00 00"
    assert transition["power_cycle_persistence"] == "pending"
    for path, digest in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _target_read(tmp_path):
    destination = tmp_path / "read"
    destination.mkdir()
    for name in ("live_transactions.json", "live_memory_map.json"):
        shutil.copyfile(INITIAL_LIVE_READ / name, destination / name)
    path = destination / "live_memory_map.json"
    document = json.loads(path.read_text())
    for offset, value in enumerate(TARGET_PREFIX):
        document["bytes"][f"0x{0x0449 + offset:04X}"] = value
    path.write_text(json.dumps(document, indent=2) + "\n")
    return destination


def test_pending_blocks_and_offline_persistence_confirmation(tmp_path):
    path = _state(tmp_path / "custom.json")
    record_custom_colour_transition(
        state_path=path, previous_hex="#0000FF", new_hex="#FF0000",
        operation_timestamp="2026-07-27T18:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T18:00:01+00:00",
    )
    opened = []
    with pytest.raises(GuardedCustomColourError, match="pending"):
        execute_custom_colour_change(
            _device(), target_hex="#FF0000", state_path=path,
            transport_factory=lambda device: opened.append(device),
        )
    assert opened == []
    evidence = _target_read(tmp_path)
    state = confirm_custom_colour_persistence(
        evidence, state_path=path,
        clock=lambda: datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc),
    )
    assert state["history"][0]["power_cycle_persistence"] == "confirmed"
    assert state["history"][0][
        "latest_confirming_live_read_directory"
    ] == str(evidence)


def test_invalid_persistence_evidence_rejected(tmp_path):
    path = _state(tmp_path / "custom.json")
    record_custom_colour_transition(
        state_path=path, previous_hex="#0000FF", new_hex="#FF0000",
        operation_timestamp="2026-07-27T18:00:00+00:00",
        result_directory=tmp_path / "result",
        updated_at="2026-07-27T18:00:01+00:00",
    )
    with pytest.raises(KnownCustomColourStateError, match="does not confirm"):
        confirm_custom_colour_persistence(INITIAL_LIVE_READ, state_path=path)


def test_project_seed_and_static_safety():
    state = load_custom_colour_state(CUSTOM_COLOUR_STATE_PATH)
    # The completed visual diagnostic is now the authoritative current state;
    # ordinary guarded-write history remains unchanged below.
    assert state["current_semantic_hex"] == "#7B2DD2"
    assert state["current_semantic_rgb"] == [123, 45, 210]
    assert state["current_device_rgb"] == "7b 2d d2"
    assert state["current_prefix"] == "7b 2d d2 01 00 01"
    correction = state["semantic_corrections"][-1]
    assert correction["original_claim"]["requested_target_hex"] == "#FF0000"
    assert correction["corrected_interpretation"][
        "new_visible_native_hex"
    ] == "#0000FF"
    transition = next(
        item for item in state["history"]
        if item.get("original_requested_target_hex") == "#FF0000"
    )
    assert transition["operation_timestamp"] == (
        "2026-07-27T13:04:47.243519+00:00"
    )
    assert transition["previous_prefix"] == "e4 a7 12 01 00 01"
    assert transition["confirmed_new_prefix"] == "00 00 ff 01 00 01"
    assert transition["previous_semantic_hex"] == "#E4A712"
    assert transition["new_semantic_hex"] == "#0000FF"
    assert transition["original_requested_target_hex"] == "#FF0000"
    assert transition["visible_result"] == "Blue"
    assert transition["power_cycle_persistence"] == "confirmed"
    assert transition["result_directory"].endswith(
        "custom_colour_0449_20260727T130447.243519Z"
    )
    assert transition["latest_confirming_live_read_directory"].endswith(
        "venus_read_20260727T130711.618278Z"
    )
    _, memory = _load_live_read(INITIAL_LIVE_READ)
    assert bytes(memory[0x0449 + offset] for offset in range(6)) == SOURCE_PREFIX
    results = safety_audit()
    assert len(results) == 8
    assert any("#FF0000" in item for item in results)
    source = (
        Path(__file__).parents[1] / "src" / "venus_profile"
        / "known_custom_colour_state.py"
    ).read_text()
    assert "discover_venus_hidraw" not in source
    assert "/dev/hidraw" not in source
    assert "_hid_iocsfeature" not in source
