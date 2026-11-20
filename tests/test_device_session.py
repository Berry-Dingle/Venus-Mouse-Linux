from __future__ import annotations

from datetime import datetime, timezone
import inspect
import os
from pathlib import Path
import stat

import pytest

from venus_profile.device_diagnostics import diagnostics
from venus_profile.device_session import (
    DeviceSession,
    DeviceSessionError,
    _BRIGHTNESS_CAPABILITY,
    create_timestamped_directory,
    load_known_read_plan,
    ownership_warnings,
    session_safety_audit,
)
from venus_profile.readonly_hidraw import (
    HidrawDevice,
    HidrawDiscoveryError,
    HidrawPermissionError,
)


def _device(
    *,
    interface: int = 2,
    usage_page: int = 0xFFA0,
    usage: int = 1,
) -> HidrawDevice:
    return HidrawDevice(
        path=Path("/mock/hidraw9"),
        sysfs_path=Path("/mock/sysfs"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=interface,
        usage_page=usage_page,
        usage=usage,
    )


class MockTransport:
    def __init__(self, responses: list[tuple[bytes, bytes]]):
        self.responses = list(responses)
        self.last_response: bytes | None = None
        self.reads: list[tuple[int, int]] = []
        self.closed = False

    def read_memory(self, address: int, length: int) -> bytes:
        self.reads.append((address, length))
        response, data = self.responses.pop(0)
        self.last_response = response
        return data

    def close(self) -> None:
        self.closed = True


def _metadata(mode: int = 0o660) -> os.stat_result:
    return os.stat_result((
        stat.S_IFCHR | mode, 0, 0, 0, os.getuid(), os.getgid(),
        0, 0, 0, 0,
    ))


def test_one_device_session_discovery():
    session = DeviceSession.discover(discovery=_device)
    assert session.device == _device()
    assert session.describe()["interface_number"] == "02"


@pytest.mark.parametrize(
    "error",
    [
        HidrawDiscoveryError("no matching device"),
        HidrawDiscoveryError("multiple matching devices"),
    ],
)
def test_zero_or_multiple_discovery_errors_propagate(error: Exception):
    def fail():
        raise error
    with pytest.raises(HidrawDiscoveryError):
        DeviceSession.discover(discovery=fail)


def test_wrong_interface_and_usage_rejected():
    with pytest.raises(DeviceSessionError, match="interface 02"):
        DeviceSession(_device(interface=1))
    with pytest.raises(DeviceSessionError, match="FFA0"):
        DeviceSession(_device(usage_page=0x0001))
    with pytest.raises(DeviceSessionError, match="usage"):
        DeviceSession(_device(usage=2))


def test_permission_failure_does_not_create_transport():
    created = []
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: created.append(device),
        stat_fn=lambda path: _metadata(),
        access_fn=lambda path, mode: False,
    )
    with pytest.raises(HidrawPermissionError, match="read/write"):
        session.open_read_session()
    assert created == []


def test_open_read_session_is_still_lazy():
    created = []
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: created.append(device),
        stat_fn=lambda path: _metadata(),
        access_fn=lambda path, mode: True,
    )
    assert session.open_read_session() is session
    assert created == []


def test_f2_read_through_shared_session_is_lazy_and_closes():
    transport = MockTransport([(b"\x02" * 9, b"\x03")])
    created = []
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: (
            created.append(device) or transport
        ),
    )
    assert created == []
    assert session.read_memory(0x044F, 1) == b"\x03"
    assert created == [_device()]
    session.close()
    assert transport.closed


def test_context_manager_closes_on_success_and_exception():
    first = MockTransport([(b"\x02" * 9, b"\x03")])
    with DeviceSession(
        _device(), transport_factory=lambda device: first
    ) as session:
        session.read_memory(0x044F, 1)
    assert first.closed

    second = MockTransport([(b"\x02" * 9, b"\x03")])
    with pytest.raises(RuntimeError, match="boom"):
        with DeviceSession(
            _device(), transport_factory=lambda device: second
        ) as session:
            session.read_memory(0x044F, 1)
            raise RuntimeError("boom")
    assert second.closed


def test_context_manager_closes_when_read_itself_fails():
    class FailingTransport:
        last_response = None
        closed = False

        def read_memory(self, address, length):
            raise RuntimeError("read failed")

        def close(self):
            self.closed = True

    transport = FailingTransport()
    with pytest.raises(RuntimeError, match="read failed"):
        with DeviceSession(
            _device(), transport_factory=lambda device: transport
        ) as session:
            session.read_memory(0x044F, 1)
    assert transport.closed


def test_use_after_close_is_rejected_without_reopening():
    created = []
    session = DeviceSession(
        _device(), transport_factory=lambda device: created.append(device)
    )
    session.close()
    with pytest.raises(DeviceSessionError, match="closed"):
        session.read_memory(0x044F, 1)
    with pytest.raises(DeviceSessionError, match="closed"):
        session.describe()
    assert created == []


def test_complete_193_read_plan_through_shared_session():
    plan = load_known_read_plan()
    responses = [
        (bytes(item["captured_response"]), bytes(item["captured_data"]))
        for item in plan
    ]
    transport = MockTransport(responses)
    fixed = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    with DeviceSession(
        _device(), transport_factory=lambda device: transport
    ) as session:
        result = session.read_full_known_plan(clock=lambda: fixed)
    assert len(result.transactions) == 193
    assert len(result.memory.values) == 4604
    assert result.memory.minimum_address == 0x002C
    assert result.memory.maximum_address == 0x1417
    assert len(transport.reads) == 193
    assert transport.closed


def test_no_public_arbitrary_sender_or_writer():
    public = {
        name for name, value in inspect.getmembers(
            DeviceSession, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }
    assert "set_feature_report" not in public
    assert "send_report" not in public
    assert "write_memory" not in public
    assert public >= {
        "describe", "check_access", "open_read_session",
        "read_memory", "read_full_known_plan", "close",
    }


def test_internal_brightness_capability_is_named_and_returns_no_sender():
    session = DeviceSession(_device())
    with pytest.raises(DeviceSessionError, match="unknown"):
        session._request_internal_capability("anything-else")
    capability = session._request_internal_capability(
        _BRIGHTNESS_CAPABILITY
    )
    assert capability == _device()
    assert not hasattr(capability, "send_report")
    assert not hasattr(capability, "write_memory")


def test_timestamped_directory_is_deterministic_and_never_overwrites(
    tmp_path: Path,
):
    fixed = datetime(2026, 7, 27, 12, 34, 56, tzinfo=timezone.utc)
    output = create_timestamped_directory(tmp_path, "operation", fixed)
    assert output.name == "operation_20260727T123456.000000Z"
    with pytest.raises(FileExistsError):
        create_timestamped_directory(tmp_path, "operation", fixed)


def test_root_owned_and_sudo_warnings(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    warnings = ownership_warnings(
        [path], current_uid=1000, effective_uid=1000
    )
    if path.stat().st_uid == 0:
        assert any("root-owned" in warning for warning in warnings)
    sudo = ownership_warnings(
        [path], current_uid=1000, effective_uid=0, sudo_user="alice"
    )
    assert any("running through sudo" in warning for warning in sudo)


def test_session_static_safety_audit():
    results = session_safety_audit()
    assert len(results) == 5
    assert any("no F1/F3/F5" in result for result in results)


def test_session_diagnostics_performs_metadata_checks_only(tmp_path: Path):
    packaged = tmp_path / "packaged.rules"
    installed = tmp_path / "installed.rules"
    packaged.write_text("rule\n")
    installed.write_text("rule\n")
    created = []
    session = DeviceSession(
        _device(),
        transport_factory=lambda device: created.append(device),
        stat_fn=lambda path: _metadata(),
        access_fn=lambda path, mode: True,
    )
    result = diagnostics(
        session,
        installed_rule=installed,
        packaged_rule=packaged,
        state_path=tmp_path / "state.json",
        output_root=tmp_path,
        group_active_fn=lambda: True,
    )
    assert result["hid_operation_performed"] is False
    assert result["venus_mouse_group_active"] is True
    assert result["udev"]["installed_matches_packaged"] is True
    assert result["access"]["read_write_access"] is True
    assert created == []
