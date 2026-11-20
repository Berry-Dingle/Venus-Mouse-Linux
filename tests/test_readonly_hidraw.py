from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from venus_profile.read_device_memory import (
    load_factory_read_plan,
    main,
    run_live_read,
    safety_audit,
    validate_offline_fixtures,
)
from venus_profile.known_device_state import (
    atomic_write_state,
    record_confirmed_transition,
)
from venus_profile.readonly_hidraw import (
    F2_COMMAND,
    HidrawDevice,
    HidrawDiscoveryError,
    HidrawProtocolError,
    ReadOnlyHidrawTransport,
    build_f2_request,
    discover_venus_hidraw,
    validate_f2_request,
    validate_f2_response,
)


DESCRIPTOR = bytes.fromhex("06 a0 ff 09 01 a1 01 c0")


def _make_hidraw(
    root: Path,
    name: str,
    *,
    interface: int,
    vendor: int = 0x04D9,
    product: int = 0xFC55,
    descriptor: bytes | None = DESCRIPTOR,
) -> tuple[Path, Path]:
    sys_class = root / "sys" / "class" / "hidraw"
    dev_root = root / "dev"
    usb = root / "sys" / "devices" / "usb1" / f"1-{name[-1]}"
    interface_dir = usb / f"1-{name[-1]}:1.{interface}"
    hid = interface_dir / f"0003:{vendor:04X}:{product:04X}.{name[-1]}"
    hid.mkdir(parents=True)
    (usb / "idVendor").write_text(f"{vendor:04x}\n", encoding="ascii")
    (usb / "idProduct").write_text(f"{product:04x}\n", encoding="ascii")
    (interface_dir / "bInterfaceNumber").write_text(
        f"{interface:02x}\n", encoding="ascii"
    )
    if descriptor is not None:
        (hid / "report_descriptor").write_bytes(descriptor)
    entry = sys_class / name
    entry.mkdir(parents=True)
    (entry / "device").symlink_to(hid)
    dev_root.mkdir(parents=True, exist_ok=True)
    node = dev_root / name
    node.touch()
    return sys_class, dev_root


def _device(path: Path = Path("/mock/hidraw9"), interface: int = 2):
    return HidrawDevice(
        path=path,
        sysfs_path=Path("/mock/sysfs"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=interface,
        usage_page=0xFFA0,
        usage=1,
    )


def _response(address: int, data: bytes, report_id: int | None = None) -> bytes:
    report_id = report_id or (2 if len(data) <= 8 else 3)
    transformed = address & ~1
    return bytes([
        report_id,
        0x08,
        transformed & 0xFF,
        ((transformed >> 8) + 0x60) & 0xFF,
        len(data) & 0xFF,
        len(data) >> 8,
        0xFA,
        0xFA,
    ]) + data


class MockFeatureIo:
    def __init__(self, responses: list[bytes]):
        self.responses = list(responses)
        self.sent: list[bytes] = []
        self.gets: list[tuple[int, int]] = []
        self.closed = False

    def send_f2(self, request: bytes) -> None:
        validate_f2_request(request)
        self.sent.append(request)

    def get_feature(self, report_id: int, length: int) -> bytes:
        self.gets.append((report_id, length))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_discovers_exact_interface2_and_usage(tmp_path: Path):
    sys_class, dev_root = _make_hidraw(
        tmp_path, "hidraw4", interface=2
    )
    found = discover_venus_hidraw(
        sys_class_hidraw=sys_class, dev_root=dev_root
    )
    assert found.path == dev_root / "hidraw4"
    assert found.vendor_id == 0x04D9
    assert found.product_id == 0xFC55
    assert found.interface_number == 2
    assert found.usage_verified


def test_discovers_vendor_usage_after_consumer_collection(
    tmp_path: Path, project_root: Path
):
    descriptor = (
        project_root / "device-baseline"
        / "factory_interface2_report_descriptor.bin"
    ).read_bytes()
    sys_class, dev_root = _make_hidraw(
        tmp_path, "hidraw4", interface=2, descriptor=descriptor
    )
    found = discover_venus_hidraw(
        sys_class_hidraw=sys_class, dev_root=dev_root
    )
    assert found.usage_verified


def test_discovery_zero_matches(tmp_path: Path):
    sys_class = tmp_path / "sys" / "class" / "hidraw"
    sys_class.mkdir(parents=True)
    with pytest.raises(HidrawDiscoveryError, match="no 04d9:fc55"):
        discover_venus_hidraw(
            sys_class_hidraw=sys_class, dev_root=tmp_path / "dev"
        )


def test_discovery_multiple_matches(tmp_path: Path):
    sys_class, dev_root = _make_hidraw(
        tmp_path, "hidraw4", interface=2
    )
    _make_hidraw(tmp_path, "hidraw5", interface=2)
    with pytest.raises(HidrawDiscoveryError, match="multiple"):
        discover_venus_hidraw(
            sys_class_hidraw=sys_class, dev_root=dev_root
        )


def test_discovery_rejects_interfaces00_and01(tmp_path: Path):
    sys_class, dev_root = _make_hidraw(
        tmp_path, "hidraw4", interface=0
    )
    _make_hidraw(tmp_path, "hidraw5", interface=1)
    with pytest.raises(HidrawDiscoveryError, match="interfaces 00 and 01"):
        discover_venus_hidraw(
            sys_class_hidraw=sys_class, dev_root=dev_root
        )


def test_discovery_rejects_wrong_usage_when_descriptor_is_available(
    tmp_path: Path,
):
    wrong = bytes.fromhex("06 a1 ff 09 01 a1 01 c0")
    sys_class, dev_root = _make_hidraw(
        tmp_path, "hidraw4", interface=2, descriptor=wrong
    )
    with pytest.raises(HidrawDiscoveryError):
        discover_venus_hidraw(
            sys_class_hidraw=sys_class, dev_root=dev_root
        )


def test_transport_constructor_rejects_wrong_interface():
    with pytest.raises(HidrawDiscoveryError, match="interface 02"):
        ReadOnlyHidrawTransport(_device(interface=1))


def test_exact_f2_request_construction():
    assert build_f2_request(0x0449, 7) == bytes.fromhex(
        "02 f2 49 04 07 00 00 00 00 00 00 00 00 00 00 00"
    )


def test_refuses_non_f2_before_backend_creation():
    calls = []
    transport = ReadOnlyHidrawTransport(
        _device(), backend_factory=lambda path: calls.append(path)
    )
    bad = bytearray(build_f2_request(0x002C, 1))
    bad[1] = 0xF3
    with pytest.raises(HidrawProtocolError, match="command must be F2"):
        transport._send_f2_request(bytes(bad))
    assert calls == []


def test_refuses_out_of_range_before_backend_creation():
    calls = []
    transport = ReadOnlyHidrawTransport(
        _device(), backend_factory=lambda path: calls.append(path)
    )
    with pytest.raises(HidrawProtocolError, match="crosses"):
        transport.read_memory(0xFFF0, 50)
    assert calls == []


def test_refuses_report4_and_report5_gets_before_backend_creation():
    calls = []
    transport = ReadOnlyHidrawTransport(
        _device(), backend_factory=lambda path: calls.append(path)
    )
    for report_id, length in ((4, 256), (5, 1024)):
        with pytest.raises(HidrawProtocolError, match="only report 2"):
            transport.get_feature_report(report_id, length)
    assert calls == []


def test_report2_short_read():
    response = _response(0x002C, b"\x00")
    backend = MockFeatureIo([response])
    transport = ReadOnlyHidrawTransport(
        _device(), backend_factory=lambda path: backend
    )
    assert transport.read_memory(0x002C, 1) == b"\x00"
    assert backend.sent == [build_f2_request(0x002C, 1)]
    assert backend.gets == [(2, 16)]


def test_report3_long_read():
    data = bytes(range(32))
    backend = MockFeatureIo([_response(0x0042, data)])
    transport = ReadOnlyHidrawTransport(
        _device(), backend_factory=lambda path: backend
    )
    assert transport.read_memory(0x0042, 32) == data
    assert backend.gets == [(3, 64)]


def test_transformed_odd_address_validation():
    data = bytes.fromhex("ff 00 00 02 05 01 02")
    assert validate_f2_response(
        _response(0x0449, data), address=0x0449, requested_length=7
    ) == data
    bad = bytearray(_response(0x0449, data))
    bad[2] = 0x49
    with pytest.raises(HidrawProtocolError, match="transformed address"):
        validate_f2_response(
            bytes(bad), address=0x0449, requested_length=7
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__(slice(6, 8), b"\x00\x00"), "marker"),
        (lambda value: value.__setitem__(4, 2), "length field"),
        (lambda value: value.__setitem__(0, 1), "unexpected response report ID"),
    ],
)
def test_malformed_response_fails_closed(mutation, message):
    value = bytearray(_response(0x002C, b"\x00"))
    mutation(value)
    with pytest.raises(HidrawProtocolError, match=message):
        validate_f2_response(
            bytes(value), address=0x002C, requested_length=1
        )


def test_short_response_fails_closed():
    with pytest.raises(HidrawProtocolError, match="short response"):
        validate_f2_response(
            bytes.fromhex("02 08 2c 60 01 00 fa fa"),
            address=0x002C,
            requested_length=1,
        )


def test_default_cli_never_discovers_or_opens(capsys):
    def forbidden():
        raise AssertionError("default mode attempted live discovery")

    assert main([], discovery=forbidden) == 0
    output = capsys.readouterr().out
    assert "offline validation passed" in output
    assert "no hidraw node was discovered or opened" in output


def test_live_mode_requires_exact_confirmation_and_does_not_open(capsys):
    called = []

    def transport_factory(device):
        called.append(device)
        raise AssertionError("transport should not be created")

    assert main(
        ["--live-read"],
        discovery=lambda: _device(),
        transport_factory=transport_factory,
        input_fn=lambda prompt: "read",
    ) == 2
    assert called == []
    assert "no hidraw node was opened" in capsys.readouterr().out


def test_yes_read_only_is_invalid_without_live(capsys):
    assert main(["--yes-read-only"]) == 2
    assert "requires --live-read" in capsys.readouterr().err


def test_factory_fixture_offline_validation():
    summary = validate_offline_fixtures()
    assert summary == {
        "read_count": 193,
        "returned_byte_count": 4604,
        "minimum_address": 0x002C,
        "maximum_address": 0x1417,
        "range_count": 24,
        "gap_count": 23,
    }


def test_deterministic_mocked_live_output(tmp_path: Path):
    plan = load_factory_read_plan()
    responses = [bytes(record["captured_response"]) for record in plan]
    device = _device(tmp_path / "mock-hidraw")
    fixed_time = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    def factory(_device_value):
        backend = MockFeatureIo(responses)
        return ReadOnlyHidrawTransport(
            _device_value, backend_factory=lambda path: backend
        )

    first = run_live_read(
        device,
        output_root=tmp_path / "first",
        transport_factory=factory,
        clock=lambda: fixed_time,
    )
    second = run_live_read(
        device,
        output_root=tmp_path / "second",
        transport_factory=factory,
        clock=lambda: fixed_time,
    )
    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files
    assert hashlib.sha256(first_files["live_memory.bin"]).hexdigest() == (
        "fa304c2aaa332e2b18491c11ed4a2d6527948114fa75a8aeb271b0ed09abf1a8"
    )
    assert set(first_files) == {
        "live_transactions.json",
        "live_reads.tsv",
        "live_memory.bin",
        "live_memory_present.bin",
        "live_memory_map.json",
        "live_gaps.txt",
        "live_read_report.md",
    }


def test_post_cycle_live_responses_match_known_state_overlay(
    tmp_path: Path, project_root: Path
):
    source = (
        project_root / "usb-captures" / "live-reads"
        / "venus_read_20260727T090950.407810Z"
        / "live_transactions.json"
    )
    document = json.loads(source.read_text())
    responses = [
        bytes.fromhex(record["response"])
        for record in document["transactions"]
    ]

    def factory(device):
        backend = MockFeatureIo(responses)
        return ReadOnlyHidrawTransport(
            device, backend_factory=lambda path: backend
        )

    state_path = tmp_path / "known.json"
    state = record_confirmed_transition(
        state_path=state_path,
        address=0x044F,
        previous_value=0x03,
        confirmed_new_value=0x02,
        operation_timestamp="2026-07-27T09:02:36+00:00",
        result_directory=tmp_path / "write",
        updated_at="2026-07-27T09:02:37+00:00",
    )
    state["history"][0]["power_cycle_persistence"] = "confirmed"
    state["history"][0]["persistence_confirmed_at"] = (
        "2026-07-27T09:10:00+00:00"
    )
    state["history"][0]["latest_confirming_live_read_directory"] = str(
        source.parent
    )
    atomic_write_state(state_path, state)
    output = run_live_read(
        _device(tmp_path / "mock-hidraw"),
        output_root=tmp_path / "result",
        transport_factory=factory,
        clock=lambda: datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc),
        known_state_path=state_path,
    )
    memory_map = json.loads((output / "live_memory_map.json").read_text())
    comparison = memory_map["comparison"]
    assert comparison["unexpected_difference_count"] == 0
    assert comparison["windows_captured_baseline_expectations"] == 4603
    assert comparison["confirmed_linux_overlay_expectations"] == 1
    override = comparison["confirmed_linux_overrides"][0]
    assert override["address"] == 0x044F
    assert override["value"] == 2


def test_safety_audit_passes():
    results = safety_audit()
    assert len(results) == 5
    assert any("no F3/F1/F5" in result for result in results)
