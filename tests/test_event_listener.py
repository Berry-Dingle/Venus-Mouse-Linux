"""Listen-only event probe: fake device only, never a real node."""

import json
import os
from pathlib import Path
import socket

import pytest

from venus_profile import event_listener as el
from venus_profile.readonly_hidraw import HidrawDevice, HidrawDiscoveryError
from venus_profile.snapshot_audit import event_listener_safety_audit

SOURCE = Path(el.__file__)


def _device(**changes):
    values = dict(path=Path("/fake/hidraw9"), sysfs_path=Path("/fake/sys"), vendor_id=0x04D9,
                  product_id=0xFC55, interface_number=0x02, usage_page=0xFFA0, usage=1)
    values.update(changes)
    return HidrawDevice(**values)


class FakeNode:
    """A datagram socket pair standing in for the hidraw node."""

    def __init__(self, reports=()):
        self.mouse, self.app = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for report in reports:
            self.mouse.send(report)
        self.opened = []

    def opener(self, path, flags):
        self.opened.append((path, flags))
        return os.dup(self.app.fileno())


def test_reports_are_read_and_interpreted_without_sending():
    node = FakeNode([bytes.fromhex("08 02 01 00 00 00 00 00"), bytes.fromhex("08 01 02 00 00 00 00 00"),
                     bytes.fromhex("08 06 04 00 00 00 00 00"), bytes.fromhex("01 e9 00 00 00 00 00 00")])
    lines = []
    document = el.listen(discovery=_device, opener=node.opener, seconds=0.3, out=lines.append)
    assert document["status"] == "ok"
    assert [item["meaning"] for item in document["reports"]] == [
        "DPI stage -> 2 (expected from DPI+/DPI-)", "profile -> 3", "polling -> 250 Hz",
        "report ID 0x01 (not an event report)"]
    (path, flags), = node.opened
    assert path == "/fake/hidraw9"
    assert flags & (os.O_WRONLY | os.O_RDWR) == 0      # read-only open
    node.mouse.setblocking(False)
    with pytest.raises(BlockingIOError):
        node.mouse.recv(64)                             # nothing was sent towards the mouse
    assert lines[0].startswith("Listening for 0 s") and lines[-1] == "Done: 4 report(s)."


def test_no_reports_is_still_ok():
    node = FakeNode()
    document = el.listen(discovery=_device, opener=node.opener, seconds=0.1, out=lambda _t: None)
    assert document["status"] == "ok" and document["reports"] == []


@pytest.mark.parametrize(("error", "status"), (
    (HidrawDiscoveryError("no 04d9:fc55 hidraw node"), "no-device"),
    (HidrawDiscoveryError("multiple matching Venus configuration interfaces found: a, b"), "multiple-devices"),
))
def test_discovery_failures(error, status):
    def discovery():
        raise error
    opened = []
    document = el.listen(discovery=discovery, opener=lambda *a: opened.append(a), seconds=0.1)
    assert document["status"] == status and opened == []


@pytest.mark.parametrize("device", (_device(usage_page=None, usage=None), _device(interface_number=1),
                                    _device(product_id=0x1234)))
def test_identity_must_be_confirmed_before_opening(device):
    opened = []
    document = el.listen(discovery=lambda: device, opener=lambda *a: opened.append(a), seconds=0.1)
    assert document["status"] == "identity-mismatch" and opened == []


def test_permission_denied():
    def opener(_path, _flags):
        raise PermissionError("denied")
    assert el.listen(discovery=_device, opener=opener, seconds=0.1)["status"] == "permission-denied"


def test_main_takes_no_options(tmp_path):
    called = []
    assert el.main(["--seconds", "999"], evidence_dir=tmp_path / "e", discovery=lambda: called.append(1)) == 2
    assert called == []


def test_main_saves_new_evidence_files(tmp_path, capsys):
    evidence = tmp_path / "live-events"
    for _ in range(2):
        node = FakeNode([bytes.fromhex("08 02 03 00 00 00 00 00")])
        assert el.main([], evidence_dir=evidence, discovery=_device, opener=node.opener, seconds=0.2) == 0
    files = sorted(evidence.glob("venus_events_*.json"))
    assert len(files) == 2
    record = json.loads(files[0].read_text())
    assert record["tool"] == "venus_profile.event_listener"
    assert record["reports"][0]["hex"] == "08 02 03 00 00 00 00 00"
    assert "Saved:" in capsys.readouterr().out


def test_failure_is_reported_and_not_saved(tmp_path, capsys):
    def discovery():
        raise HidrawDiscoveryError("no 04d9:fc55 hidraw node")
    assert el.main([], evidence_dir=tmp_path / "e", discovery=discovery) == 1
    assert "status: no-device" in capsys.readouterr().out
    assert not (tmp_path / "e").exists()


@pytest.mark.parametrize(("report", "meaning"), (
    ("08 02 00", "DPI stage -> 1 (expected from DPI+/DPI-)"),
    ("08 06 08", "polling -> 125 Hz"),
    ("08 06 01", "polling -> 1000 Hz"),
    ("08 06 03", "polling code 0x03 (unknown)"),
    ("08 09 00 02 00 01", "lighting mode changed (raw bytes; mapping not verified)"),
    ("08 ff 11", "0xFF event (handled separately by Windows; not analysed)"),
    ("08 42 00", "unknown event kind 0x42"),
    ("07 01 02", "report ID 0x07 (not an event report)"),
))
def test_describe_report(report, meaning):
    assert el.describe_report(bytes.fromhex(report)) == meaning


def test_listener_audit_passes():
    assert len(event_listener_safety_audit()) == 3


@pytest.mark.parametrize(("original", "tampered"), (
    ("os.O_RDONLY | os.O_NONBLOCK", "os.O_RDWR | os.O_NONBLOCK"),
    ("report = os.read(fd, READ_SIZE)", "report = os.read(fd, READ_SIZE); os.write(fd, b'x')"),
    ("    discover_venus_hidraw,\n)", "    discover_venus_hidraw,\n    ReadOnlyHidrawTransport,\n)"),
    ("import select\n", "import select\nimport fcntl\n"),
    ("import select\n", "import select\nfrom .device_session import DeviceSession\n"),
    ("    out(INSTRUCTIONS", "    opener('/dev/hidraw0', os.O_RDONLY)\n    out(INSTRUCTIONS"),
    ("args = sys.argv[1:] if argv is None else argv", "args = []"),
))
def test_listener_audit_rejects_tampering(tmp_path, original, tampered):
    source = SOURCE.read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "event_listener.py"
    fake.write_text(source.replace(original, tampered, 1), encoding="utf-8")
    with pytest.raises(RuntimeError):
        event_listener_safety_audit(fake)
