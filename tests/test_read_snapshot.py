"""Read-only snapshot helper: packaged plan, statuses, end-to-end with a fake transport.

No test touches a device: sessions and transports are fakes that replay the
saved factory capture.
"""

import json
from pathlib import Path

import pytest

from venus_profile import read_snapshot as rs
from venus_profile.device_session import DeviceSession, DeviceSessionError, load_known_read_plan
from venus_profile.readonly_hidraw import (
    HidrawDevice,
    HidrawDiscoveryError,
    HidrawPermissionError,
    HidrawProtocolError,
)
from venus_profile.snapshot_audit import snapshot_safety_audit
from evidence_paths import EVIDENCE  # noqa: E402

PROJECT = EVIDENCE
CAPTURE = PROJECT / "usb-captures" / "analysis" / "factory_transactions.json"


def test_packaged_read_plan_is_the_captured_plan_unchanged():
    captured = json.loads(CAPTURE.read_text(encoding="utf-8"))["feature_reads"]
    packaged = json.loads(rs.packaged_read_plan_path().read_text(encoding="utf-8"))["feature_reads"]
    keys = ("address", "requested_length", "response", "returned_data")
    assert [{k: r[k] for k in keys} for r in captured] == packaged
    assert len(load_known_read_plan(rs.packaged_read_plan_path())) == 193


class _ReplayTransport:
    """Answers each F2 read with the captured factory response."""

    def __init__(self, _device):
        plan = load_known_read_plan(rs.packaged_read_plan_path())
        self._answers = {(int(r["address"]), int(r["length"])): r for r in plan}
        self.last_response = None
        self.requests = []

    def read_memory(self, address, length):
        if (address, length) == (0x0020, 10):            # the fixed extra read (scroll lines)
            self.requests.append((address, length))
            return bytes.fromhex("01 00 01 00 01 00 01 00 01 00")
        record = self._answers[(address, length)]
        self.requests.append((address, length))
        self.last_response = record["captured_response"]
        return record["captured_data"]

    def close(self):
        pass


def _fake_device():
    return HidrawDevice(Path("/nonexistent/venus-test-node"), Path("/nonexistent/sys"),
                        0x04D9, 0xFC55, 0x02, 0xFFA0, 1)


def _session_factory(**kwargs):
    import os

    class _Stat:
        st_mode = 0o20660
        st_uid = os.getuid()
        st_gid = os.getgid()

    def factory():
        return DeviceSession(
            _fake_device(), transport_factory=_ReplayTransport,
            stat_fn=lambda _path: _Stat(), access_fn=kwargs.get("access_fn", lambda _path, _mode: True))
    return factory


def test_end_to_end_snapshot_reproduces_the_factory_memory():
    document = rs.take_snapshot(_session_factory())
    assert document["status"] == "ok" and document["read_count"] == 194   # 193 captured + scroll
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    memory_map = json.loads((PROJECT / "usb-captures/analysis/factory_memory_map.json").read_text())
    base = memory_map["base_address"]
    data = (PROJECT / "usb-captures/analysis/factory_memory.bin").read_bytes()
    present = (PROJECT / "usb-captures/analysis/factory_memory_present.bin").read_bytes()
    expected = {base + i: data[i] for i in range(len(data)) if present[i]}
    expected.update({0x0020 + i: v for i, v in enumerate(bytes.fromhex("01 00 01 00 01 00 01 00 01 00"))})
    assert values == expected
    assert document["read_count"] == 194                      # 193 captured + 1 scroll read


def test_permission_denied_is_reported():
    document = rs.take_snapshot(_session_factory(access_fn=lambda _path, _mode: False))
    assert document["status"] == "permission-denied"


@pytest.mark.parametrize(("error", "status"), (
    (HidrawDiscoveryError("no 04d9:fc55 hidraw node on interface 02 ..."), "no-device"),
    (HidrawDiscoveryError("multiple matching Venus configuration interfaces found: a, b"), "multiple-devices"),
    (DeviceSessionError("session requires confirmed usage page FFA0, usage 1"), "identity-mismatch"),
    (OSError("sysfs unreadable"), "read-failed"),
))
def test_discovery_failures_map_to_statuses(error, status):
    def factory():
        raise error
    document = rs.take_snapshot(factory)
    assert document["status"] == status and document["message"]


class _FailingSession:
    def __init__(self, error):
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def describe(self):
        return {"path": "x"}

    def open_read_session(self):
        return self

    def read_full_known_plan(self, **_kwargs):
        raise self._error


@pytest.mark.parametrize(("error", "status"), (
    (HidrawProtocolError("bad marker"), "read-failed"),
    (HidrawPermissionError("no rw"), "permission-denied"),
    (OSError("I/O error"), "read-failed"),
))
def test_read_failures_never_return_a_partial_ok(error, status):
    document = rs.take_snapshot(lambda: _FailingSession(error))
    assert document["status"] == status
    assert "memory" not in document


def test_main_prints_one_json_document_and_takes_no_options(capsys):
    assert rs.main([], session_factory=_session_factory()) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1 and json.loads(printed[0])["status"] == "ok"
    assert rs.main(["--write"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "read-failed"


def test_snapshot_helper_audit_proves_read_only():
    lines = snapshot_safety_audit()
    assert len(lines) == 3
    source = (Path(rs.__file__)).read_text(encoding="utf-8")
    assert "argparse" not in source and "F3" not in source
