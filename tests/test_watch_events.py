"""Listen-only event helper for the GUI: fake device only, never a real node."""

import json
import os
from pathlib import Path
import socket
import tempfile

import pytest

from venus_profile import watch_events as we
from venus_profile.app_store import AppStore
from venus_profile.readonly_hidraw import HidrawDevice, HidrawDiscoveryError
from venus_profile.snapshot_audit import watch_events_safety_audit
from venus_profile.workspace import VenusWorkspace
from evidence_paths import EVIDENCE  # noqa: E402

SOURCE = Path(we.__file__)


def _device(**changes):
    values = dict(path=Path("/fake/hidraw9"), sysfs_path=Path("/fake/sys"), vendor_id=0x04D9,
                  product_id=0xFC55, interface_number=0x02, usage_page=0xFFA0, usage=1)
    values.update(changes)
    return HidrawDevice(**values)


class Rig:
    """Fake hidraw node (datagram socket pair) plus the GUI's stdin pipe."""

    def __init__(self, reports=()):
        self.mouse, self.app = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        for report in reports:
            self.mouse.send(bytes.fromhex(report))
        self.control_read, self.control_write = os.pipe()
        self.opened = []
        self.emitted = []

    def opener(self, path, flags):
        self.opened.append((path, flags))
        return os.dup(self.app.fileno())

    def run(self, **overrides):
        kwargs = dict(discovery=_device, opener=self.opener, control_fd=self.control_read,
                      emit=self.emitted.append)
        kwargs.update(overrides)
        return we.watch(**kwargs)


@pytest.mark.parametrize(("report", "event"), (
    ("08 02 00 00 00 00 00 00", {"event": "dpi-stage", "stage": 0}),
    ("08 02 04 00 00 00 00 00", {"event": "dpi-stage", "stage": 4}),
    ("08 01 03 00 00 00 00 00", {"event": "profile", "profile": 3}),
    ("08 02 05 00 00 00 00 00", None),       # out of range: ignored
    ("08 01 09 00 00 00 00 00", None),
    ("08 06 04 00 00 00 00 00", None),       # polling: not passed on
    ("01 e9 00 00 00 00 00 00", None),       # consumer report
    ("08 02", None),
))
def test_decode_event(report, event):
    assert we.decode_event(bytes.fromhex(report)) == event


def test_streams_confirmed_events_from_the_saved_live_run():
    evidence = EVIDENCE / "usb-captures" / "live-events" / \
        "venus_events_20261119T013459.176208Z.json"
    if not evidence.exists():
        pytest.skip("saved live event run not present")
    reports = [item["hex"] for item in json.loads(evidence.read_text())["reports"]]
    rig = Rig(reports)
    rig.mouse.close()                         # then the mouse "goes away"
    assert rig.run() == 1
    assert rig.emitted[0] == {"status": "listening"}
    events = rig.emitted[1:-1]
    assert [e.get("stage", e.get("profile")) for e in events] == [1, 2, 3, 2, 1, 0, 1, 2, 3, 4, 0]
    assert [e["event"] for e in events] == ["dpi-stage"] * 6 + ["profile"] * 5
    assert rig.emitted[-1]["status"] == "disconnected"
    (path, flags), = rig.opened
    assert flags & (os.O_WRONLY | os.O_RDWR) == 0


def test_exits_quietly_when_the_gui_closes_stdin():
    rig = Rig(["08 02 01 00 00 00 00 00"])
    os.close(rig.control_write)
    assert rig.run() == 0
    assert rig.emitted[0] == {"status": "listening"}
    assert all("status" not in item for item in rig.emitted[1:])
    rig.mouse.setblocking(False)
    with pytest.raises(BlockingIOError):
        rig.mouse.recv(64)                    # nothing was sent towards the mouse


@pytest.mark.parametrize(("error", "status"), (
    (HidrawDiscoveryError("no 04d9:fc55 hidraw node"), "no-device"),
    (HidrawDiscoveryError("multiple matching Venus configuration interfaces found: a, b"), "multiple-devices"),
))
def test_discovery_failures(error, status):
    def discovery():
        raise error
    rig = Rig()
    assert rig.run(discovery=discovery) == 1
    assert rig.emitted == [{"status": status, "message": str(error)}] and rig.opened == []


@pytest.mark.parametrize("device", (_device(usage_page=None, usage=None), _device(interface_number=0)))
def test_identity_must_be_confirmed_before_opening(device):
    rig = Rig()
    assert rig.run(discovery=lambda: device) == 1
    assert rig.emitted[0]["status"] == "identity-mismatch" and rig.opened == []


def test_permission_denied():
    def opener(_path, _flags):
        raise PermissionError("denied")
    rig = Rig()
    assert rig.run(opener=opener) == 1
    assert rig.emitted[0]["status"] == "permission-denied"


def test_main_takes_no_options(capsys):
    called = []
    assert we.main(["--any"], discovery=lambda: called.append(1)) == 2
    assert called == [] and "no options" in capsys.readouterr().out


def test_helper_audit_passes():
    assert len(watch_events_safety_audit()) == 3


@pytest.mark.parametrize(("original", "tampered"), (
    ("os.O_RDONLY | os.O_NONBLOCK", "os.O_WRONLY | os.O_NONBLOCK"),
    ("report = os.read(fd, READ_SIZE)", "report = os.read(fd, READ_SIZE); os.write(fd, b'x')"),
    ("    discover_venus_hidraw,\n)", "    discover_venus_hidraw,\n    ReadOnlyHidrawTransport,\n)"),
    ("import select\n", "import select\nimport fcntl\n"),
    ("import select\n", "import select\nfrom .device_session import DeviceSession\n"),
    ("    emit({\"status\": STATUS_LISTENING})", "    open('/tmp/x', 'w')\n    emit({\"status\": STATUS_LISTENING})"),
    ("    emit({\"status\": STATUS_LISTENING})", "    opener('/dev/hidraw0', os.O_RDONLY)\n    emit({\"status\": STATUS_LISTENING})"),
    ("args = sys.argv[1:] if argv is None else argv", "args = []"),
))
def test_helper_audit_rejects_tampering(tmp_path, original, tampered):
    source = SOURCE.read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "watch_events.py"
    fake.write_text(source.replace(original, tampered, 1), encoding="utf-8")
    with pytest.raises(RuntimeError):
        watch_events_safety_audit(fake)


# --------------------------------------------------------------- workspace

def _workspace() -> VenusWorkspace:
    root = Path(tempfile.mkdtemp())
    return VenusWorkspace.open(AppStore(root / "store", macro_library_dir=root / "Macros"))


def test_mouse_dpi_stage_is_not_an_edit():
    workspace = _workspace()
    assert workspace.apply_mouse_dpi_stage(3, 4) is True
    assert workspace.profile(3).selected_dpi_stage_raw == 4
    assert workspace.edited_slots() == ()
    assert workspace.loaded_name(3) == "From mouse"
    assert workspace.apply_mouse_dpi_stage(3, 4) is False


def test_mouse_dpi_stage_keeps_other_edits_and_cancel_keeps_the_stage():
    workspace = _workspace()
    workspace.profile(2).set_led_color_rgb(1, 2, 3)
    workspace.edited()
    workspace.apply_mouse_dpi_stage(2, 3)
    assert workspace.edited_slots() == (2,)
    workspace.cancel_edits(2)
    assert workspace.profile(2).led_color_rgb != (1, 2, 3)     # the edit is cancelled
    assert workspace.profile(2).selected_dpi_stage_raw == 3     # the mouse's stage stays
