"""One-shot lighting helper: exact reports, guards, read-back, audit (fakes only).

No test touches a device. The session is a real ``DeviceSession`` (so its
identity checks run) over a fake transport backed by a simulated memory; the
write fd is a fake that applies validated F3 reports to the same memory.
"""

import io
import json
import os
from pathlib import Path

import pytest

from venus_profile import lighting_preview_plan as plan
from venus_profile import preview_lighting as pl
from venus_profile.device_session import DeviceSession, DeviceSessionError
from venus_profile.readonly_hidraw import HidrawDevice, HidrawDiscoveryError
from venus_profile.snapshot_audit import preview_lighting_safety_audit

FACTORY = {   # captured factory lighting (record + brightness) per profile
    1: "ff0000020501" "02", 2: "0000ff020501" "02", 3: "00ff00020501" "02",
    4: "ff00ff020501" "02", 5: "ffff00020501" "02",
}


class Memory:
    def __init__(self, lighting=None):
        self.values = {}
        for profile, text in (lighting or FACTORY).items():
            for offset, value in enumerate(bytes.fromhex(text)):
                self.values[plan.RECORD_ADDRESSES[profile - 1] + offset] = value
        self.values[plan.ACTIVE_PROFILE_ADDRESS] = 0          # profile 1 active
        for i, v in enumerate(bytes.fromhex("02 00 02 00 02 00 02 00 02 00")):   # factory polling 500 Hz
            self.values[0x0032 + i] = v
        for i, v in enumerate(bytes.fromhex("01 00 01 00 01 00 01 00 01 00")):   # scroll lines (user's mouse)
            self.values[0x0020 + i] = v
        for slot in range(15):                                  # macro slots: slot 1 holds "a", others empty
            body = (bytes.fromhex("840400060100040400") if slot == 0 else b"").ljust(200, b"\0")
            for i, v in enumerate(body):
                self.values[plan.macro_slot_address(slot) + i] = v
        for base in plan.BUTTON_PROFILE_BASES:                  # buttons: 1-2 clicks, 5 DPI+, 7 macro, others key 'a'
            for index, offset in enumerate(plan.BUTTON_OFFSETS, start=1):
                record = {1: "81000000", 2: "82000000", 5: "8a000000", 7: "91000100"}.get(index, "90000400")
                for i, v in enumerate(bytes.fromhex(record)):
                    self.values[base + offset + i] = v
        for base in plan.DPI_PROFILE_BASES:                     # factory DPI blocks (stage byte + 5 records)
            block = bytes.fromhex("0000" "011600000000" "012d00000000" "015a00000000" "015c01000000"
                                  "017c04000000")[:32]
            for i, v in enumerate(block):
                self.values[base + 2 + i] = v
        self.values[plan.ACTIVE_PROFILE_ADDRESS + 1] = 0
        self.events = []
        self.ignore_writes = False
        self.shifted_replies = {}     # address -> count of replies served one byte early


class FakeTransport:
    def __init__(self, memory):
        self.memory = memory
        self.last_response = None

    def read_memory(self, address, length):
        self.memory.events.append(("F2", address, length))
        if self.memory.shifted_replies.get(address):
            # Seen live: the reply holds the data one byte earlier (0x0448 for
            # 0x0449); the header echoes the even address either way.
            self.memory.shifted_replies[address] -= 1
            return bytes([0x80]) + bytes(self.memory.values[address + i] for i in range(length - 1))
        return bytes(self.memory.values[address + i] for i in range(length))

    def close(self):
        pass


class FakeWriter:
    def __init__(self, memory, path):
        self.memory, self.path = memory, path
        memory.events.append(("open-writer",))

    def submit(self, report):
        pl.validate_preview_report(report)
        self.memory.events.append(("send", bytes(report)))
        if report in (pl.LIGHTING_FINALISATION_REPORT,) + pl.PROFILE_SWITCH_SEQUENCE \
                or self.memory.ignore_writes:
            return
        address, length = report[2] | (report[3] << 8), report[4]
        for i in range(length):
            self.memory.values[address + i] = report[8 + i]          # also the 200-byte macro body

    def close(self):
        self.memory.events.append(("close-writer",))


def _device(**overrides):
    values = dict(path=Path("/nonexistent/venus-test-node"), sysfs_path=Path("/nonexistent/sys"),
                  vendor_id=0x04D9, product_id=0xFC55, interface_number=0x02, usage_page=0xFFA0, usage=1)
    values.update(overrides)
    return HidrawDevice(**values)


def _session_factory(memory, **device):
    class _Stat:
        st_mode = 0o20660
        st_uid = os.getuid()
        st_gid = os.getgid()

    def factory():
        return DeviceSession(_device(**device), transport_factory=lambda _d: FakeTransport(memory),
                             stat_fn=lambda _p: _Stat(), access_fn=lambda _p, _m: True)
    return factory


def _run(memory, targets, **device):
    sleeps = []
    document = pl.run(plan.request_document(targets), _session_factory(memory, **device),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def _target(profile=1, rgb=(0, 0, 255), mode="full-brightness", brightness=2, speed=None):
    return plan.LightingTarget(profile, rgb, mode, brightness, speed)


# ----------------------------------------------------------------- reports

def test_exact_record_report_matches_the_proven_linux_write():
    report = pl.build_record_report(1, bytes.fromhex("ff0000010001"))
    assert report.hex(" ") == "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00"   # GUARDED_LED_COLOUR_WRITE.md


def test_brightness_and_other_profile_reports_match_windows_captures():
    assert pl.build_brightness_report(1, 3).hex(" ") == "02 f3 4f 04 01 00 00 00 03 00 00 00 00 00 00 00"
    assert pl.build_record_report(2, bytes.fromhex("0000ff020501")).hex(" ") == \
        "02 f3 51 04 06 00 00 00 00 00 ff 02 05 01 00 00"                          # led_colour_red_to_blue 8212
    assert pl.build_brightness_report(5, 2).hex(" ") == "02 f3 6f 04 01 00 00 00 02 00 00 00 00 00 00 00"
    assert pl.LIGHTING_FINALISATION_REPORT.hex(" ") == "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"


@pytest.mark.parametrize("report", (
    "02 f3 46 04 02 00 00 00 00 00 00 00 00 00 00 00",     # 0x0446: unexplained, never written
    "02 f3 49 04 07 00 00 00 ff 00 00 01 00 01 03 00",     # record + brightness in one write
    "02 f3 49 04 06 00 00 00 ff 00 00 02 07 01 00 00",     # unknown breathing speed
    "02 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 01",     # padding
    "02 f3 4f 04 01 00 00 00 04 00 00 00 00 00 00 00",     # brightness 4
    "02 f3 4a 04 06 00 00 00 ff 00 00 01 00 01 00 00",     # misaligned address
    "02 f3 82 00 04 00 00 00 01 00 00 00 00 00 00 00",     # button record
    "02 f3 49 04 06 01 00 00 ff 00 00 01 00 01 00 00",     # reserved header byte
    "02 f5 02 00 00 00 00 00 00 00 00 00 00 00 00 00",     # F5 other than the macro bracket
    "02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 01",     # F5 00 with padding
    "02 f1 02 00 00 00 00 00 00 00 00 00 00 00 00 00",     # other F1 (02 00: no confirmed sender)
    "02 f3 2c 00 02 00 00 00 05 00 00 00 00 00 00 00",     # profile 6
    "02 f3 2c 00 02 00 00 00 01 01 00 00 00 00 00 00",     # 0x002D not 00
    "02 f3 2c 00 01 00 00 00 01 00 00 00 00 00 00 00",     # wrong length
    "02 f2 49 04 06 00 00 00 00 00 00 00 00 00 00 00",     # F2 is not a write
    "03 f3 49 04 06 00 00 00 ff 00 00 01 00 01 00 00",     # report 3
))
def test_every_other_report_is_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(bytes.fromhex(report))
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(bytes.fromhex(report)[:15])


def test_real_write_backend_validates_before_its_ioctl(tmp_path):
    calls = []
    writer = pl._LightingWriteIo(tmp_path / "node", opener=lambda _p, _f: 99,
                                 ioctl=lambda *args: calls.append(args) or 16)
    with pytest.raises(pl.LightingWriteError):
        writer.submit(bytes.fromhex("02f50200000000000000000000000000"))
    assert calls == []
    writer.submit(pl.LIGHTING_FINALISATION_REPORT)
    assert len(calls) == 1
    writer._fd = -1                              # nothing real to close


# ----------------------------------------------------------------- transaction

def test_one_colour_change_is_pre_read_f3_f1_read_back():
    memory = Memory()
    document, sleeps = _run(memory, (_target(rgb=(0, 0, 255), mode="breathing", speed=5, brightness=2),))
    assert document["status"] == "ok", document
    reads = [("F2", a, 7) for a in plan.RECORD_ADDRESSES for _ in range(2)]   # two agreeing replies each
    assert memory.events == reads + [
        ("open-writer",),
        ("send", bytes.fromhex("02f34904060000000000ff0205010000")),
        ("send", pl.LIGHTING_FINALISATION_REPORT),
        ("close-writer",),
    ] + reads
    assert sleeps == [0.010, 0.010]
    assert document["written"] == [{"profile": 1, "before": FACTORY[1], "after": "0000ff02050102"}]
    assert document["records"]["1"] == "0000ff02050102" and document["records"]["2"] == FACTORY[2]


def test_only_changed_fields_are_written():
    memory = Memory()
    document, sleeps = _run(memory, (
        _target(1, (255, 0, 0), "breathing", 3, 5),        # brightness only
        _target(2, (0, 0, 255), "breathing", 2, 5),        # unchanged
        _target(4, (1, 2, 3), "wave", 0),                  # record and brightness
    ))
    assert document["status"] == "ok"
    sent = [event[1] for event in memory.events if event[0] == "send"]
    assert [pl.validate_preview_report(r) for r in sent] == [
        "lighting-brightness-p1", "lighting-record-p4", "lighting-brightness-p4", "lighting-finalisation"]
    assert sleeps == [0.010] * 4
    assert [entry["profile"] for entry in document["written"]] == [1, 4]


def test_nothing_differs_writes_nothing():
    memory = Memory()
    document, sleeps = _run(memory, (_target(2, (0, 0, 255), "breathing", 2, 5),))
    assert document["status"] == "unchanged"
    assert all(event[0] == "F2" for event in memory.events) and sleeps == []
    assert document["records"] == {str(p): v for p, v in FACTORY.items()}


def test_unknown_lighting_on_the_mouse_is_refused_before_opening_the_writer():
    memory = Memory({**FACTORY, 3: "00ff00020701" "02"})      # unknown breathing speed 7
    document, _ = _run(memory, (_target(3),))
    assert document["status"] == "refused" and "profile 3" in document["message"]
    assert all(event[0] == "F2" for event in memory.events)


def test_unknown_lighting_on_an_untouched_profile_does_not_block():
    memory = Memory({**FACTORY, 3: "00ff00990000" "07"})
    document, _ = _run(memory, (_target(1),))
    assert document["status"] == "ok" and document["records"]["3"] == "00ff0099000007"


def test_a_one_off_shifted_reply_is_not_mistaken_for_unknown_lighting():
    memory = Memory({**FACTORY, 1: "e40a63020201" "03"})       # the live case: Rose, breathing fast
    memory.shifted_replies[0x0449] = 1                         # first reply: 80 e4 0a 63 02 02 01
    document, _ = _run(memory, (_target(1, (0xE4, 0x0A, 0x63), "breathing", 1, 2),))
    assert document["status"] == "ok", document
    assert document["written"] == [{"profile": 1, "before": "e40a6302020103", "after": "e40a6302020101"}]


def test_a_shifted_reply_during_read_back_is_not_a_false_mismatch():
    memory = Memory()

    class Writer(FakeWriter):
        def submit(self, report):
            super().submit(report)
            if report == pl.LIGHTING_FINALISATION_REPORT:
                memory.shifted_replies[0x0449] = 1

    document = pl.run(plan.request_document((_target(1),)), _session_factory(memory),
                      backend_factory=lambda path: Writer(memory, path), sleeper=lambda _s: None)
    assert document["status"] == "ok", document


def test_replies_that_never_agree_stop_before_any_write():
    memory = Memory()

    class Flaky(FakeTransport):
        count = 0

        def read_memory(self, address, length):
            Flaky.count += 1
            memory.shifted_replies[address] = Flaky.count % 2     # alternate shifted / normal
            return super().read_memory(address, length)

    def factory():
        session = _session_factory(memory)()
        session._transport_factory = lambda _d: Flaky(memory)
        return session
    document = pl.run(plan.request_document((_target(1),)), factory,
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=lambda _s: None)
    assert document["status"] == "write-failed" and "unstable read at 0x0449" in document["message"]
    assert len([e for e in memory.events if e[0] == "F2"]) == pl.MAX_READS_PER_RECORD
    assert not any(e[0] in ("open-writer", "send") for e in memory.events)


def test_read_back_mismatch_is_reported_without_retry():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _run(memory, (_target(1),))
    assert document["status"] == "mismatch" and "profile(s) 1" in document["message"]
    assert len([e for e in memory.events if e[0] == "send"]) == 2          # one F3 + F1, no retry


def test_a_neighbour_changing_is_a_mismatch():
    memory = Memory()

    class Writer(FakeWriter):
        def submit(self, report):
            super().submit(report)
            memory.values[plan.RECORD_ADDRESSES[1]] = 0x12                  # profile 2 disturbed

    document = pl.run(plan.request_document((_target(1),)), _session_factory(memory),
                      backend_factory=lambda path: Writer(memory, path), sleeper=lambda _s: None)
    assert document["status"] == "mismatch" and "2" in document["message"]


def test_wrong_identity_is_refused_without_reads():
    memory = Memory()
    document, _ = _run(memory, (_target(1),), product_id=0xFC56)
    assert document["status"] == "identity-mismatch" and memory.events == []
    document, _ = _run(memory, (_target(1),), usage_page=0xFF00)
    assert document["status"] == "identity-mismatch" and memory.events == []


@pytest.mark.parametrize(("error", "status"), (
    (HidrawDiscoveryError("no Venus hidraw device found"), "no-device"),
    (HidrawDiscoveryError("multiple Venus devices"), "multiple-devices"),
    (DeviceSessionError("bad"), "identity-mismatch"),
))
def test_discovery_failures(error, status):
    def factory():
        raise error
    assert pl.run(plan.request_document((_target(),)), factory)["status"] == status


def test_bad_request_never_discovers():
    def factory():
        raise AssertionError("must not discover")
    document = pl.run('{"version": 1, "profiles": [{"address": 1097}]}', factory)
    assert document["status"] == "refused"


def test_main_reads_stdin_and_takes_no_options(capsys):
    memory = Memory()
    assert pl.main(["--anything"], io.StringIO(""), _session_factory(memory)) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert memory.events == []
    oversize = io.StringIO("x" * (plan.MAX_REQUEST_BYTES + 10))
    assert pl.main([], oversize, _session_factory(memory)) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_main_unchanged_request_end_to_end(capsys):
    memory = Memory()
    request = io.StringIO(plan.request_document((_target(2, (0, 0, 255), "breathing", 2, 5),)))
    assert pl.main([], request, _session_factory(memory)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"


# ----------------------------------------------------------------- active profile

def _switch(memory, profile):
    sleeps = []
    document = pl.run(plan.profile_request_document(profile), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def test_profile_switch_is_read_f3_f1_read_back():
    memory = Memory()
    document, sleeps = _switch(memory, 3)
    assert document["status"] == "ok" and document["active_profile"] == 3 and document["previous_profile"] == 1
    read = ("F2", 0x002C, 1)
    assert memory.events == [read, read, ("open-writer",),
                             ("send", bytes.fromhex("02f32c00020000000200000000000000")),
                             ("send", bytes.fromhex("02f10201000000000000000000000000")),
                             ("send", bytes.fromhex("02f10204000000000000000000000000")),   # hid.exe 0x410cf0
                             ("send", bytes.fromhex("02f10202000000000000000000000000")),
                             ("send", bytes.fromhex("02f10208000000000000000000000000")),
                             ("close-writer",), read, read]
    assert sleeps == [0.010] * 5
    assert memory.values[0x002C] == 2 and memory.values[0x002D] == 0


def test_switch_is_sent_even_if_memory_already_shows_the_target():
    """The mouse's memory may lag a physical button press; the GUI's events are authoritative."""
    memory = Memory()                                         # 0x002C = 0 (possibly stale)
    document, sleeps = _switch(memory, 1)
    assert document["status"] == "ok" and document["active_profile"] == 1
    sent = [event[1] for event in memory.events if event[0] == "send"]
    assert sent[0] == pl.build_profile_report(1) and tuple(sent[1:]) == pl.PROFILE_SWITCH_SEQUENCE
    assert sleeps == [0.010] * 5


def test_out_of_range_active_byte_is_refused():
    memory = Memory()
    memory.values[0x002C] = 7
    document, _ = _switch(memory, 2)
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


def test_profile_read_back_mismatch_is_reported():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _switch(memory, 4)
    assert document["status"] == "mismatch" and document["active_profile"] == 1
    assert len([e for e in memory.events if e[0] == "send"]) == 5         # no retry


def test_profile_report_templates():
    assert pl.build_profile_report(1).hex(" ") == "02 f3 2c 00 02 00 00 00 00 00 00 00 00 00 00 00"  # = captured Apply
    assert pl.build_profile_report(5).hex(" ") == "02 f3 2c 00 02 00 00 00 04 00 00 00 00 00 00 00"
    assert pl.validate_preview_report(pl.PROFILE_FINALISATION_REPORT) == "profile-finalisation"


# ----------------------------------------------------------------- polling

def _poll(memory, values):
    sleeps = []
    document = pl.run(plan.polling_request_document(values), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def test_polling_change_is_read_two_f3_f1_08_read_back():
    memory = Memory()
    document, sleeps = _poll(memory, (1, 2, 2, 2, 8))
    assert document["status"] == "ok" and document["polling"] == [1, 2, 2, 2, 8]
    reads = [("F2", 0x0032, 5)] * 2 + [("F2", 0x0038, 3)] * 2
    assert memory.events == reads + [
        ("open-writer",),
        ("send", bytes.fromhex("02f33200060000000100020002000000")),   # hid.exe 0x410230
        ("send", bytes.fromhex("02f33800040000000200080000000000")),
        ("send", bytes.fromhex("02f10208000000000000000000000000")),
        ("close-writer",)] + reads
    assert sleeps == [0.010] * 3
    assert bytes(memory.values[0x0032 + i] for i in range(10)).hex(" ") == "01 00 02 00 02 00 02 00 08 00"


def test_polling_unchanged_writes_nothing():
    memory = Memory()
    document, sleeps = _poll(memory, (2, 2, 2, 2, 2))
    assert document["status"] == "unchanged" and sleeps == []
    assert all(event[0] == "F2" for event in memory.events)


@pytest.mark.parametrize(("address", "value"), ((0x0032, 3), (0x0033, 1), (0x0039, 4), (0x003A, 0)))
def test_unknown_polling_bytes_are_refused_before_writing(address, value):
    memory = Memory()
    memory.values[address] = value
    document, _ = _poll(memory, (1, 1, 1, 1, 1))
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


def test_polling_read_back_mismatch():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _poll(memory, (1, 2, 2, 2, 2))
    assert document["status"] == "mismatch" and len([e for e in memory.events if e[0] == "send"]) == 3


@pytest.mark.parametrize("report", (
    "02 f3 32 00 06 00 00 00 03 00 02 00 02 00 00 00",     # 3 is not an enum
    "02 f3 32 00 06 00 00 00 02 01 02 00 02 00 00 00",     # companion
    "02 f3 32 00 06 00 00 00 02 00 02 00 02 00 01 00",     # padding
    "02 f3 32 00 05 00 00 00 02 00 02 00 02 00 00 00",     # length
    "02 f3 38 00 04 00 00 00 02 00 02 00 01 00 00 00",     # padding (0x003C)
    "02 f3 34 00 02 00 00 00 02 00 00 00 00 00 00 00",     # other polling address
))
def test_bad_polling_reports_are_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(bytes.fromhex(report))


# ----------------------------------------------------------------- DPI stage values (T4)

def _dpi(memory, stages):
    sleeps = []
    document = pl.run(plan.dpi_request_document(stages), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def test_factory_records_decode():
    assert plan.dpi_raw_from_record(bytes.fromhex("0116000000")) is not None
    assert plan.dpi_raw_from_record(bytes.fromhex("0016000000")) is None        # disabled: not handled yet
    assert plan.dpi_raw_from_record(bytes.fromhex("0116000001")) is None


def test_dpi_change_writes_only_that_record_then_f1_10_and_02():
    memory = Memory()
    raw = 60
    document, sleeps = _dpi(memory, ((1, 1, raw),))
    assert document["status"] == "ok", document
    record = plan.dpi_record(raw)
    block = [("F2", 0x0042, 32)] * 2
    assert memory.events == block + [
        ("open-writer",),
        ("send", bytes.fromhex("02f3440005000000") + record + bytes(3)),
        ("send", bytes.fromhex("02f10210000000000000000000000000")),
        ("send", bytes.fromhex("02f10202000000000000000000000000")),
        ("close-writer",)] + block
    assert sleeps == [0.010] * 3
    assert bytes(memory.values[0x0044 + i] for i in range(5)) == record
    assert memory.values[0x0042] == 0                                   # start-up stage untouched


def test_unchanged_dpi_writes_nothing():
    memory = Memory()
    current = plan.dpi_raw_from_record(bytes.fromhex("012d000000"))
    document, sleeps = _dpi(memory, ((3, 2, current),))
    assert document["status"] == "unchanged" and sleeps == []


def test_unknown_or_disabled_record_on_the_mouse_is_refused():
    memory = Memory()
    memory.values[0x0044] = 0                                            # stage 1 disabled
    document, _ = _dpi(memory, ((1, 1, 50),))
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


def test_dpi_neighbour_change_is_a_mismatch():
    memory = Memory()

    class Writer(FakeWriter):
        def submit(self, report):
            super().submit(report)
            memory.values[0x004A] = 0                                    # stage 2 disturbed

    document = pl.run(plan.dpi_request_document(((1, 1, 60),)), _session_factory(memory),
                      backend_factory=lambda path: Writer(memory, path), sleeper=lambda _s: None)
    assert document["status"] == "mismatch"


@pytest.mark.parametrize("report", (
    "02 f3 44 00 05 00 00 00 00 16 00 00 00 00 00 00",     # enable 00 (not handled yet)
    "02 f3 44 00 05 00 00 00 01 16 01 00 00 00 00 00",     # lookup/selector pair not in the table
    "02 f3 44 00 05 00 00 00 01 16 00 00 00 00 00 01",     # padding
    "02 f3 45 00 05 00 00 00 01 16 00 00 00 00 00 00",     # misaligned
    "02 f3 42 00 02 00 00 00 05 00 00 00 00 00 00 00",     # start-up stage 5 (only 0-4)
    "02 f3 42 00 02 00 00 00 01 01 00 00 00 00 00 00",     # start-up stage companion not 00
    "02 f3 42 00 01 00 00 00 01 00 00 00 00 00 00 00",     # start-up stage length 1
    "02 f3 43 00 02 00 00 00 01 00 00 00 00 00 00 00",     # start-up stage misaligned
    "02 f3 42 00 02 00 00 00 01 00 00 00 00 00 00 01",     # start-up stage padding
))
def test_bad_dpi_reports_are_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(bytes.fromhex(report))


# --------------------------------------------------------------- T5: DPI start-up stage

def _dpi_stage(memory, entries, writer=None):
    sleeps = []
    document = pl.run(plan.dpi_stage_request_document(entries), _session_factory(memory),
                      backend_factory=writer or (lambda path: FakeWriter(memory, path)), sleeper=sleeps.append)
    return document, sleeps


def test_start_up_stage_is_f3_base2_then_f1_02_then_10_as_windows():
    memory = Memory()
    document, sleeps = _dpi_stage(memory, ((1, 2), (3, 0)))         # P3 already on stage 1: unchanged
    assert document["status"] == "ok", document
    reads = [("F2", 0x0042, 2)] * 2 + [("F2", 0x01B2, 2)] * 2
    assert memory.events == reads + [
        ("open-writer",),
        ("send", bytes.fromhex("02f34200020000000200000000000000")),
        ("send", bytes.fromhex("02f10202000000000000000000000000")),
        ("send", bytes.fromhex("02f10210000000000000000000000000")),
        ("close-writer",)] + reads
    assert sleeps == [pl.INTER_COMMAND_DELAY_SECONDS] * 3
    assert (memory.values[0x0042], memory.values[0x0043]) == (2, 0)
    assert document["dpi_stage"] == [{"profile": 1, "stage": 2}, {"profile": 3, "stage": 0}]


def test_unchanged_start_up_stage_writes_nothing():
    memory = Memory()
    document, sleeps = _dpi_stage(memory, ((2, 0),))
    assert document["status"] == "unchanged" and sleeps == []
    assert all(event[0] == "F2" for event in memory.events)


@pytest.mark.parametrize("current", ((5, 0), (0, 1), (0xFF, 0xFF)))
def test_unknown_start_up_stage_on_the_mouse_is_refused(current):
    memory = Memory()
    memory.values[0x0102], memory.values[0x0103] = current
    document, _ = _dpi_stage(memory, ((2, 3),))
    assert document["status"] == "refused" and all(event[0] == "F2" for event in memory.events)


def test_start_up_stage_read_back_mismatch():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _dpi_stage(memory, ((5, 4),))
    assert document["status"] == "mismatch" and "5" in document["message"]


@pytest.mark.parametrize("text", (
    '{"version": 1, "dpi_stage": [{"profile": 1, "stage": 5}]}',
    '{"version": 1, "dpi_stage": [{"profile": 6, "stage": 0}]}',
    '{"version": 1, "dpi_stage": [{"profile": 1, "stage": 0}, {"profile": 1, "stage": 1}]}',
    '{"version": 1, "dpi_stage": [{"profile": 1, "stage": 0, "address": 66}]}',
    '{"version": 1, "dpi_stage": []}',
    '{"version": 1, "dpi_stage": [{"profile": 1, "stage": true}]}',
))
def test_bad_start_up_stage_requests_never_reach_the_mouse(text):
    memory = Memory()
    document = pl.run(text, _session_factory(memory), backend_factory=lambda path: FakeWriter(memory, path),
                      sleeper=lambda _s: None)
    assert document["status"] == "refused" and memory.events == []


# ----------------------------------------------------------------- buttons (Stage 1)

def _buttons(memory, entries):
    sleeps = []
    document = pl.run(plan.buttons_request_document(entries), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def test_button_change_is_one_f3_per_changed_record_and_no_finaliser():
    memory = Memory()
    document, sleeps = _buttons(memory, ((1, 5, bytes.fromhex("89000000")), (1, 6, bytes.fromhex("90000400"))))
    assert document["status"] == "ok", document
    reads = [("F2", 0x0092, 4)] * 2 + [("F2", 0x0096, 4)] * 2
    assert memory.events == reads + [
        ("open-writer",),
        ("send", bytes.fromhex("02f39200040000008900000000000000")),   # same report as the T1 tool
        ("close-writer",)] + reads
    assert sleeps == [0.010]


def test_unknown_button_record_on_the_mouse_is_refused():
    memory = Memory()
    for i, v in enumerate(bytes.fromhex("7f010203")):
        memory.values[0x0092 + i] = v
    document, _ = _buttons(memory, ((1, 5, bytes.fromhex("89000000")),))
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


def test_button_read_back_mismatch():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _buttons(memory, ((2, 3, bytes.fromhex("84000000")),))
    assert document["status"] == "mismatch" and "P2B3" in document["message"]


@pytest.mark.parametrize("report", (
    "02 f3 92 00 04 00 00 00 7f 01 02 03 00 00 00 00",     # unknown shape
    "02 f3 92 00 04 00 00 00 89 00 00 00 00 00 00 01",     # padding
    "02 f3 93 00 04 00 00 00 89 00 00 00 00 00 00 00",     # misaligned
    "02 f3 ca 00 04 00 00 00 89 00 00 00 00 00 00 00",     # 0x00CA: the per-profile gap, not a button
    "02 f3 92 00 04 00 00 00 91 0f 01 00 00 00 00 00",     # macro slot 16
))
def test_bad_button_reports_are_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(bytes.fromhex(report))


# ----------------------------------------------------------------- macro slots (Stage 3)

def _macros(memory, entries):
    sleeps = []
    document = pl.run(plan.macros_request_document(entries), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def _macro_body(*records):
    data = b"".join(bytes.fromhex(r) for r in records)
    return data + bytes(200 - len(data))


def test_macro_upload_is_f5_00_report4_f5_01_as_captured():
    memory = Memory()
    body = _macro_body("841e00", "060100", "041e00")
    document, sleeps = _macros(memory, ((1, body),))
    assert document["status"] == "ok", document
    reads = [("F2", 0x0540 + 50 * k, 50) for k in range(4) for _ in range(2)]
    report = bytes.fromhex("04f34005c8000000") + body + bytes(48)
    assert memory.events == reads + [
        ("open-writer",),
        ("send", bytes.fromhex("02f50000000000000000000000000000")),
        ("send", report),
        ("send", bytes.fromhex("02f50100000000000000000000000000")),
        ("close-writer",)] + reads
    assert sleeps == [0.100, 0.010, 0.100]
    assert bytes(memory.values[0x0540 + i] for i in range(200)) == body


def test_captured_slot_1_report_is_reproduced_exactly():
    body = _macro_body("840400", "060100", "040400")
    report = pl.build_macro_report(0, body)
    assert report[:8].hex(" ") == "04 f3 78 04 c8 00 00 00" and len(report) == 256 and not any(report[208:])


def test_clearing_a_slot_writes_the_factory_empty_body():
    memory = Memory()
    document, _ = _macros(memory, ((0, plan.EMPTY_MACRO_BODY),))
    assert document["status"] == "ok"
    assert not any(memory.values[0x0478 + i] for i in range(200))


def test_unchanged_macro_slot_writes_nothing():
    memory = Memory()
    document, sleeps = _macros(memory, ((2, plan.EMPTY_MACRO_BODY),))
    assert document["status"] == "unchanged" and sleeps == []


@pytest.mark.parametrize("unknown", ("ffff01", "ff0000", "fe ff 00", "910000"))
def test_unknown_body_on_the_mouse_is_refused(unknown):
    memory = Memory()
    for offset, value in enumerate(bytes.fromhex(unknown)):
        memory.values[plan.macro_slot_address(7) + offset] = value
    document, _ = _macros(memory, ((7, _macro_body("840400", "040400")),))
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


def test_factory_filler_in_slot_8_is_written_over_like_windows():
    memory = Memory()
    memory.values[plan.macro_slot_address(7)] = 0xFF
    memory.values[plan.macro_slot_address(7) + 1] = 0xFF                # the real slot 8: ff ff 00 ...
    body = _macro_body("840500", "060700", "040500")                    # what Windows wrote there (§5.1)
    document, _ = _macros(memory, ((7, body),))
    assert document["status"] == "ok", document
    assert bytes(memory.values[plan.macro_slot_address(7) + i] for i in range(200)) == body


def test_macro_read_back_mismatch():
    memory = Memory()
    memory.ignore_writes = True
    document, _ = _macros(memory, ((3, _macro_body("840400", "040400")),))
    assert document["status"] == "mismatch" and "4" in document["message"]


@pytest.mark.parametrize("report", (
    bytes.fromhex("04f3780400c80000") + _macro_body("840400") + bytes(48),          # header order wrong
    bytes.fromhex("04f37904c8000000") + _macro_body("840400") + bytes(48),          # odd address
    bytes.fromhex("04f33010c8000000") + _macro_body("840400") + bytes(48),          # 0x1030: past slot 15
    bytes.fromhex("04f37804c8000000") + _macro_body("ffff00") + bytes(48),          # unknown body
    bytes.fromhex("04f37804c8000000") + _macro_body("840400") + bytes(47) + b"\x01",  # padding
    bytes.fromhex("05f37804c8000000") + _macro_body("840400") + bytes(48),          # report 5
))
def test_bad_macro_reports_are_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(report)


def test_real_backend_sends_256_bytes_for_a_macro_upload(tmp_path):
    calls = []
    writer = pl._LightingWriteIo(tmp_path / "node", opener=lambda _p, _f: 99,
                                 ioctl=lambda fd, request, buffer, mutate: calls.append((request, len(buffer))) or 256)
    writer.submit(pl.build_macro_report(0, _macro_body("840400", "040400")))
    from venus_profile.readonly_hidraw import _hid_iocsfeature
    assert calls == [(_hid_iocsfeature(256), 256)]
    writer._fd = -1


# ----------------------------------------------------------------- scroll lines (T6)

def _scroll(memory, values):
    sleeps = []
    document = pl.run(plan.scroll_request_document(values), _session_factory(memory),
                      backend_factory=lambda path: FakeWriter(memory, path), sleeper=sleeps.append)
    return document, sleeps


def test_scroll_change_is_report3_then_the_apply_tail():
    memory = Memory()
    document, sleeps = _scroll(memory, (3, 1, 1, 1, 1))
    assert document["status"] == "ok" and document["scroll"] == [3, 1, 1, 1, 1]
    reads = [("F2", 0x0020, 10)] * 2
    report = bytes.fromhex("03f320000a000000" "03000100010001000100") + bytes(46)
    assert memory.events == reads + [
        ("open-writer",),
        ("send", report),
        ("send", bytes.fromhex("02f10204000000000000000000000000")),
        ("send", bytes.fromhex("02f10201000000000000000000000000")),
        ("send", bytes.fromhex("02f10202000000000000000000000000")),
        ("send", bytes.fromhex("02f10208000000000000000000000000")),
        ("close-writer",)] + reads
    assert sleeps == [0.010] * 5


def test_captured_apply_scroll_report_is_reproduced():
    report = pl.build_scroll_report((1, 1, 1, 1, 1))
    assert report[:18].hex(" ") == "03 f3 20 00 0a 00 00 00 01 00 01 00 01 00 01 00 01 00" and len(report) == 64


def test_unchanged_scroll_writes_nothing():
    memory = Memory()
    document, sleeps = _scroll(memory, (1, 1, 1, 1, 1))
    assert document["status"] == "unchanged" and sleeps == []


@pytest.mark.parametrize(("address", "value"), ((0x0020, 0), (0x0021, 1), (0x0028, 64)))
def test_unknown_scroll_bytes_are_refused(address, value):
    memory = Memory()
    memory.values[address] = value
    document, _ = _scroll(memory, (2, 2, 2, 2, 2))
    assert document["status"] == "refused" and all(e[0] == "F2" for e in memory.events)


@pytest.mark.parametrize("report", (
    bytes.fromhex("03f320000a000000" "00000100010001000100") + bytes(46),      # 0 lines
    bytes.fromhex("03f320000a000000" "40000100010001000100") + bytes(46),      # 64 lines
    bytes.fromhex("03f320000a000000" "01010100010001000100") + bytes(46),      # companion
    bytes.fromhex("03f320000a000000" "01000100010001000100") + bytes(45) + b"\x01",  # padding
    bytes.fromhex("03f322000a000000" "01000100010001000100") + bytes(46),      # other address
    bytes.fromhex("03f220000a000000" "01000100010001000100") + bytes(46),      # F2 in report 3
))
def test_bad_scroll_reports_are_rejected(report):
    with pytest.raises(pl.LightingWriteError):
        pl.validate_preview_report(report)


# ----------------------------------------------------------------- audit

def test_safety_audit_passes():
    assert len(preview_lighting_safety_audit()) == 6


HELPER = Path(pl.__file__)
PLAN = Path(plan.__file__)


@pytest.mark.parametrize(("original", "tampered"), (
    ('    def submit(self, report: bytes) -> None:\n        validate_preview_report(report)\n',
     '    def submit(self, report: bytes) -> None:\n        pass\n'),
    ('LIGHTING_FINALISATION_REPORT = bytes.fromhex("02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00")',
     'LIGHTING_FINALISATION_REPORT = bytes.fromhex("02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00")'),
    ('    writer = backend_factory(session.device.path)\n    try:\n        for report in reports:',
     '    writer = backend_factory(session.device.path)\n    other = backend_factory(session.device.path)\n'
     '    try:\n        for report in reports:'),
    ('PROFILE_FINALISATION_REPORT = bytes.fromhex("02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00")',
     'PROFILE_FINALISATION_REPORT = bytes.fromhex("02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00")'),
    ('            writer.submit(MACRO_BEGIN_REPORT)\n            sleeper(MACRO_BRACKET_DELAY_SECONDS)\n',
     ''),                                                           # upload without the F5 00 bracket
    ('        writer.submit(PROFILE_APPLY_REPORT_08)\n',
     '        writer.submit(PROFILE_APPLY_REPORT_08)\n        writer.submit(MACRO_END_REPORT)\n'),   # F5 elsewhere
    ('PROFILE_APPLY_REPORT_08 = bytes.fromhex("02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00")',
     'PROFILE_APPLY_REPORT_08 = bytes.fromhex("02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00")'),
    ('    current = _stable_read(session, ACTIVE_PROFILE_ADDRESS, 1)[0]\n',
     '    current = 0\n'),
    ('    if current is None:\n        return _result(STATUS_REFUSED, "polling bytes',
     '    if False:\n        return _result(STATUS_REFUSED, "polling bytes'),
    ('    args = sys.argv[1:] if argv is None else argv\n', '    args = []\n'),
    ('    return {profile: _stable_read(session, address)\n',
     '    return {profile: session.read_memory(address, LIGHTING_READ_LENGTH)\n'),
    ('        if len(replies) >= 2 and replies[-1] == replies[-2]:\n',
     '        if replies:\n'),
    ('    session.open_read_session()\n    current = _read_lighting(session)\n',
     '    session.open_read_session()\n    writer = backend_factory(session.device.path)\n'
     '    current = _read_lighting(session)\n'),
    # T5: the guard must come before the writer; the finaliser and read-back must stay.
    ("        if current[profile] is None:\n            return _result(STATUS_REFUSED, f\"profile {profile} start-up",
     "        if False:\n            return _result(STATUS_REFUSED, f\"profile {profile} start-up"),
    ("        for finalisation in DPI_STAGE_SEQUENCE:\n", "        for finalisation in ():\n"),
    ("    read_back = {profile: _stable_read(session, dpi_stage_select_address(profile), DPI_STAGE_SELECT_LENGTH)\n",
     "    read_back = {profile: wanted[profile].to_bytes(1, 'little') + bytes(1) for profile in profiles} or {\n"
     "        profile: _stable_read(session, dpi_stage_select_address(profile), DPI_STAGE_SELECT_LENGTH)\n"),
))
def test_audit_rejects_a_tampered_helper(tmp_path, original, tampered):
    source = HELPER.read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "preview_lighting.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        preview_lighting_safety_audit(fake)


@pytest.mark.parametrize(("original", "tampered"), (
    ("import json\n", "import json\nimport os\n"),
    ("FLASH_COMPANION = 5\n", "FLASH_COMPANION = 5\nCOMMAND = 0xF3\n"),
))
def test_audit_rejects_a_tampered_plan_module(tmp_path, original, tampered):
    source = PLAN.read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "lighting_preview_plan.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        preview_lighting_safety_audit(plan_path=fake)
