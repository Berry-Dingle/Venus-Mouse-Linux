"""Read-only probe of unread bytes (T2): fixed requests, confirmation, evidence (fakes only)."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from venus_profile import read_probe as rp
from venus_profile.device_session import DeviceSession
from venus_profile.readonly_hidraw import HidrawDevice
from venus_profile.snapshot_audit import read_probe_safety_audit

MEMORY = {0x0020 + i: v for i, v in enumerate(bytes.fromhex("01 00 03 00 01 00 01 00 05 00"))}
MEMORY.update({0x002C: 2, 0x002D: 0, 0x003E: 0, 0x003F: 0, 0x0446: 0, 0x0447: 0, 0x0448: 0x80, 0x0449: 0xE4})


class FakeTransport:
    def __init__(self, log, flaky=False):
        self.log, self.flaky, self.count, self.last_response = log, flaky, 0, None

    def read_memory(self, address, length):
        self.log.append((address, length))
        self.count += 1
        data = bytes(MEMORY[address + i] for i in range(length))
        if self.flaky and self.count % 2:
            data = bytes([data[0] ^ 1]) + data[1:]
        return data

    def close(self):
        pass


def _factory(log, flaky=False, product_id=0xFC55):
    class _Stat:
        st_mode, st_uid, st_gid = 0o20660, os.getuid(), os.getgid()

    def factory():
        device = HidrawDevice(Path("/nonexistent/node"), Path("/nonexistent/sys"), 0x04D9, product_id, 2, 0xFFA0, 1)
        return DeviceSession(device, transport_factory=lambda _d: FakeTransport(log, flaky),
                             stat_fn=lambda _p: _Stat(), access_fn=lambda _p, _m: True)
    return factory


CLOCK = lambda: datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)  # noqa: E731


def test_offline_by_default_never_discovers(capsys):
    def factory():
        raise AssertionError("must not discover")
    assert rp.main([], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert "0x0020 length 10" in out and "no hidraw node was discovered" in out


def test_live_probe_reads_only_the_fixed_table_twice_each(tmp_path, capsys):
    log = []
    assert rp.main(["--live-read"], session_factory=_factory(log), input_fn=lambda _p: "READ",
                   output_root=tmp_path, clock=CLOCK) == 0
    assert log == [(a, n) for a, n, _ in rp.PROBE_READS for _ in range(2)]
    evidence = json.loads(next(tmp_path.glob("probe_*/probe.json")).read_text())
    assert [r["data"] for r in evidence["reads"]] == ["01 00 03 00 01 00 01 00 05 00", "02 00", "00 00",
                                                       "00 00 80 e4"]
    out = capsys.readouterr().out
    assert "P2=03 00" in out and "profile 3" in out and "0x0448 = 0x80" in out


def test_wrong_confirmation_reads_nothing(tmp_path):
    log = []
    assert rp.main(["--live-read"], session_factory=_factory(log), input_fn=lambda _p: "read",
                   output_root=tmp_path) == 2
    assert log == [] and not list(tmp_path.iterdir())


def test_unstable_replies_fail(tmp_path):
    log = []
    with pytest.raises(rp.ProbeError):
        rp.main(["--live-read"], session_factory=_factory(log, flaky=True), input_fn=lambda _p: "READ",
                output_root=tmp_path)
    assert len(log) == rp.MAX_READS_PER_REQUEST


def test_identity_is_checked_before_anything():
    from venus_profile.device_session import DeviceSessionError
    with pytest.raises(DeviceSessionError):
        rp.main(["--live-read"], session_factory=_factory([], product_id=0x1234), input_fn=lambda _p: "READ")


def test_no_address_option():
    with pytest.raises(SystemExit):
        rp.main(["--live-read", "--address", "0x82"])


def test_safety_audit_passes():
    assert len(read_probe_safety_audit()) == 4


PROBE = Path(rp.__file__)


@pytest.mark.parametrize(("original", "tampered"), (
    ('    (0x0446, 4, "0x0446-0x0447 unexplained, 0x0448, P1 red"),\n',
     '    (0x0446, 4, "0x0446-0x0447 unexplained, 0x0448, P1 red"),\n    (0x0082, 4, "button"),\n'),
    ('    parser.add_argument("--live-read", action="store_true",',
     '    parser.add_argument("--address", type=int)\n    parser.add_argument("--live-read", action="store_true",'),
    ('MAX_READS_PER_REQUEST = 4\n', 'MAX_READS_PER_REQUEST = 4\nF3 = 0xF3\n'),
    ('        replies.append(session.read_memory(address, length))\n',
     '        replies.append(session.read_memory(address, length))\n'
     '        session._request_internal_capability("x")\n'),
))
def test_audit_rejects_a_tampered_probe(tmp_path, original, tampered):
    source = PROBE.read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "read_probe.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_probe_safety_audit(fake)
