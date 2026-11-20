"""Pure lighting encoding for live writes: allow-list, strict request parsing."""

import json

import pytest

from venus_profile import apply_plan
from venus_profile import lighting_preview_plan as plan
from venus_profile.lighting_state import DEVICE_LED_MODE_PREFIX_NAMES
from venus_profile.workspace import VenusWorkspace
from venus_profile.app_store import AppStore


def _target(**overrides):
    values = {"profile": 1, "rgb": (255, 0, 0), "mode": "full-brightness", "brightness": 3}
    values.update(overrides)
    return plan.LightingTarget(**values)


def test_field_table_is_the_evidenced_layout():
    assert plan.RECORD_ADDRESSES == tuple(apply_plan.lighting_address(p) for p in range(1, 6))
    assert plan.BRIGHTNESS_ADDRESSES == tuple(a + apply_plan.LIGHTING_BRIGHTNESS_OFFSET
                                              for a in plan.RECORD_ADDRESSES)


@pytest.mark.parametrize(("mode", "speed", "suffix"), (
    ("full-brightness", None, "010001"),
    ("breathing", 8, "020801"), ("breathing", 5, "020501"), ("breathing", 2, "020201"),
    ("breathing", 0, "020001"),
    ("wave", None, "030001"), ("flash", None, "040501"),
    ("rainbow", None, "020003"), ("off", None, "000000"),
))
def test_records_use_only_evidenced_suffixes(mode, speed, suffix):
    target = _target(rgb=(0x7B, 0x2D, 0xD2), mode=mode, speed=speed, brightness=2)
    assert target.record().hex() == "7b2dd2" + suffix
    assert target.lighting_bytes().hex() == "7b2dd2" + suffix + "02"
    assert plan.suffix_is_known(bytes.fromhex(suffix))


def test_allow_list_agrees_with_the_decoder_names_and_apply_preview():
    named = set(DEVICE_LED_MODE_PREFIX_NAMES)
    assert named <= plan.ALLOWED_SUFFIXES
    assert plan.ALLOWED_SUFFIXES - named == {bytes((2, s, 1)) for s in (8, 2, 0)}   # other breathing speeds

    class _Profile:
        breathing_speed_raw = 5
        flash_state_raw = 5
    for pfd_mode, name in plan.PFD_MODE_NAMES.items():
        expected = apply_plan.PFD_MODE_TO_DEVICE_PREFIX[pfd_mode](_Profile())
        target = _target(mode=name, speed=5 if name == "breathing" else None)
        assert target.record()[3:] == expected


@pytest.mark.parametrize("overrides", (
    {"profile": 0}, {"profile": 6}, {"profile": True}, {"rgb": (256, 0, 0)}, {"rgb": (0, 0)},
    {"rgb": (0, 0, -1)}, {"mode": "strobe"}, {"brightness": 4}, {"brightness": -1},
    {"mode": "breathing"}, {"mode": "breathing", "speed": 7}, {"mode": "wave", "speed": 5},
))
def test_values_outside_the_evidence_are_rejected(overrides):
    with pytest.raises(plan.LightingRequestError):
        _target(**overrides)


def test_known_lighting_requires_suffix_and_brightness():
    assert plan.lighting_is_known(bytes.fromhex("ff0000010001" "03"))
    assert not plan.lighting_is_known(bytes.fromhex("ff0000020701" "03"))   # unknown breathing speed
    assert not plan.lighting_is_known(bytes.fromhex("ff0000040601" "03"))   # unknown flash value
    assert not plan.lighting_is_known(bytes.fromhex("ff0000010001" "04"))   # brightness 4
    assert not plan.lighting_is_known(bytes.fromhex("ff0000010001"))        # short
    assert not plan.lighting_is_known(None)


def test_request_round_trip():
    targets = (_target(), _target(profile=3, mode="breathing", speed=8, rgb=(1, 2, 3), brightness=0))
    text = plan.request_document(targets)
    assert plan.parse_request(text) == targets
    assert json.loads(text)["profiles"][1] == {"profile": 3, "rgb": [1, 2, 3], "mode": "breathing",
                                               "brightness": 0, "speed": 8}


@pytest.mark.parametrize("document", (
    "", "not json", "[]", json.dumps({"version": 1}),
    json.dumps({"version": 2, "profiles": [{"profile": 1, "rgb": [0, 0, 0], "mode": "off", "brightness": 0}]}),
    json.dumps({"version": 1, "profiles": []}),
    json.dumps({"version": 1, "profiles": [{"profile": 1, "rgb": [0, 0, 0], "mode": "off"}]}),
    json.dumps({"version": 1, "profiles": [{"profile": 1, "rgb": [0, 0, 0], "mode": "off", "brightness": 0,
                                            "address": 1097}]}),
    json.dumps({"version": 1, "profiles": [{"profile": 1, "rgb": "ff0000", "mode": "off", "brightness": 0}]}),
    json.dumps({"version": 1, "profiles": [{"profile": 1, "rgb": [0, 0, 0], "mode": "off", "brightness": 0}] * 2}),
    json.dumps({"version": 1, "profiles": [], "raw": "02f3"}),
    json.dumps({"version": 1, "profiles": [{"profile": 1, "rgb": [0, 0, 0], "mode": "off", "brightness": 0}],
                "pad": "x" * 5000}),
))
def test_parse_request_is_strict(document):
    with pytest.raises(plan.LightingRequestError):
        plan.parse_request(document)


def test_target_from_workspace_profiles(tmp_path):
    workspace = VenusWorkspace.open(AppStore(tmp_path / "store", macro_library_dir=tmp_path / "Macros"))
    profile = workspace.profile(2)
    target = plan.target_from_profile(2, profile)
    assert target is not None and target.profile == 2
    assert target.rgb == tuple(profile.led_color_rgb) and target.brightness == profile.brightness_raw
    profile.set_led_mode(2)                          # Rainbow
    assert plan.target_from_profile(2, profile).mode == "rainbow"


def test_target_from_profile_skips_unencodable_values():
    class _Profile:
        led_mode_raw = 4             # Flash
        flash_state_raw = 6          # no evidence for anything but 5
        breathing_speed_raw = 5
        led_color_rgb = (1, 2, 3)
        brightness_raw = 3
    assert plan.target_from_profile(1, _Profile()) is None
    _Profile.led_mode_raw, _Profile.breathing_speed_raw = 0, 7
    assert plan.target_from_profile(1, _Profile()) is None
    _Profile.led_mode_raw = 9
    assert plan.target_from_profile(1, _Profile()) is None


def test_profile_requests_are_strict():
    assert plan.parse_any_request(plan.profile_request_document(4)) == ("profile", 4)
    kind, targets = plan.parse_any_request(plan.request_document((_target(),)))
    assert kind == "lighting" and targets == (_target(),)
    for bad in (0, 6, True, "3"):
        with pytest.raises(plan.LightingRequestError):
            plan.profile_request_document(bad)
    for document in ('{"version": 1, "active_profile": 6}', '{"version": 2, "active_profile": 1}',
                     '{"version": 1, "active_profile": 1, "address": 44}', '{"version": 1, "active_profile": "1"}'):
        with pytest.raises(plan.LightingRequestError):
            plan.parse_any_request(document)


def test_polling_reads_and_requests():
    assert plan.polling_from_reads(bytes.fromhex("0200040001"), bytes.fromhex("080002")) == (2, 4, 1, 8, 2)
    assert plan.polling_from_reads(bytes.fromhex("0201040001"), bytes.fromhex("080002")) is None   # companion
    assert plan.polling_from_reads(bytes.fromhex("0300040001"), bytes.fromhex("080002")) is None   # enum
    assert plan.parse_any_request(plan.polling_request_document((1, 2, 4, 8, 1))) == ("polling", (1, 2, 4, 8, 1))
    for bad in ((1, 2, 3, 4, 8), (1, 2), (1, 1, 1, 1, 1, 1)):
        with pytest.raises(plan.LightingRequestError):
            plan.polling_request_document(bad)
    for document in ('{"version": 1, "polling": [1, 1, 1, 1]}', '{"version": 1, "polling": "11111"}',
                     '{"version": 1, "polling": [1, 1, 1, 1, 1], "address": 50}'):
        with pytest.raises(plan.LightingRequestError):
            plan.parse_any_request(document)


def test_dpi_lookup_matches_apply_plan_and_requests_are_strict():
    assert plan.DPI_LOOKUP == tuple(apply_plan._DPI_LOOKUP)
    for raw in range(107):
        assert plan.dpi_record(raw)[1:3] == bytes(apply_plan.dpi_lookup(raw))
    assert len({plan.dpi_record(raw) for raw in range(107)}) == 107
    assert plan.dpi_record_address(1, 1) == 0x0044 and plan.dpi_record_address(5, 5) == 0x0310 + 4 + 24
    assert plan.parse_any_request(plan.dpi_request_document(((2, 3, 50),))) == ("dpi", ((2, 3, 50),))
    for bad in (((0, 1, 1),), ((1, 6, 1),), ((1, 1, 107),), ((1, 1, 5), (1, 1, 6)), ()):
        with pytest.raises(plan.LightingRequestError):
            plan.dpi_request_document(bad)
    for document in ('{"version": 1, "dpi": [{"profile": 1, "stage": 1}]}',
                     '{"version": 1, "dpi": [{"profile": 1, "stage": 1, "raw": 5, "enable": 0}]}',
                     '{"version": 1, "dpi": []}'):
        with pytest.raises(plan.LightingRequestError):
            plan.parse_any_request(document)


def test_button_grammar_and_equivalence():
    from venus_profile.button_assignments import BUTTON_OFFSETS, PROFILE_BASES
    from venus_profile.device_decode import _NAMED_TEMPLATES
    assert plan.NAMED_BUTTON_RECORDS == frozenset(_NAMED_TEMPLATES)
    assert plan.BUTTON_OFFSETS == tuple(BUTTON_OFFSETS)
    assert plan.BUTTON_PROFILE_BASES == tuple(PROFILE_BASES[p] for p in range(1, 6))
    known = ("00000000", "8a000000", "90001700", "8f020400", "99810401", "99040301", "91000100", "9144ffff")
    unknown = ("7f010203", "90010400", "90000000", "8f100400", "99810001", "910f0100", "91c00100", "8a0000")
    assert all(plan.button_record_is_known(bytes.fromhex(r)) for r in known)
    assert not any(plan.button_record_is_known(bytes.fromhex(r)) for r in unknown)
    eq = plan.button_records_equivalent
    assert eq(bytes.fromhex("99810401"), bytes.fromhex("99810301"))      # fire count kept on the mouse
    assert not eq(bytes.fromhex("99810401"), bytes.fromhex("99820301"))  # different trigger
    assert eq(bytes.fromhex("9144ffff"), bytes.fromhex("91040100"))      # same macro slot
    assert not eq(bytes.fromhex("91040100"), bytes.fromhex("91050100"))
    assert plan.parse_any_request(plan.buttons_request_document(((1, 20, bytes.fromhex("8c000000")),))) == \
        ("buttons", ((1, 20, bytes.fromhex("8c000000")),))
    for document in ('{"version": 1, "buttons": [{"profile": 1, "button": 21, "record": "8a000000"}]}',
                     '{"version": 1, "buttons": [{"profile": 1, "button": 5, "record": "7f010203"}]}',
                     '{"version": 1, "buttons": [{"profile": 1, "button": 5, "record": "8a00"}]}',
                     '{"version": 1, "buttons": [{"profile": 1, "button": 5, "record": "8a000000", "addr": 1}]}'):
        with pytest.raises(plan.LightingRequestError):
            plan.parse_any_request(document)


def _body(*records):
    data = b"".join(bytes.fromhex(r) for r in records)
    return data + bytes(200 - len(data))


def test_macro_body_grammar_and_requests():
    from venus_profile.macroset import report4_address
    assert all(plan.macro_slot_address(s) == report4_address(s) for s in range(15))
    assert plan.macro_slot_address(14) + 200 == 0x1030                     # never past slot 15
    captured = _body("840400", "060100", "040400")                          # the captured slot-1 upload
    assert plan.macro_body_is_known(captured) and plan.macro_body_is_known(plan.EMPTY_MACRO_BODY)
    assert plan.macro_body_is_known(_body("810200", "060c00", "010200"))
    bad = [_body("ffff00"),                       # seen in a real slot 8: unknown
           _body("840000"),                       # key with usage 0
           _body("810300"),                       # mouse mask 3
           _body("840400", "000000", "040400"),   # action after padding
           captured[:189] + b"\x01" + bytes(10),   # trailing byte
           captured[:199]]
    assert not any(plan.macro_body_is_known(body) for body in bad)
    # The factory slot-8 filler counts as a free slot but can never be written.
    assert plan.FACTORY_SLOT_FILLER == _body("ffff00") and len(plan.FACTORY_SLOT_FILLER) == 200
    assert plan.macro_slot_is_free(plan.FACTORY_SLOT_FILLER) and plan.macro_slot_is_free(plan.EMPTY_MACRO_BODY)
    assert not any(plan.macro_slot_is_free(body) for body in (captured, _body("ffff01"), _body("ff0000"),
                                                               plan.FACTORY_SLOT_FILLER[:199] + b"\x01"))
    with pytest.raises(plan.LightingRequestError):
        plan.macros_request_document(((7, plan.FACTORY_SLOT_FILLER),))
    assert plan.parse_any_request(plan.macros_request_document(((0, captured),))) == ("macros", ((0, captured),))
    for document in ('{"version": 1, "macros": [{"slot": 15, "body": "' + "00" * 200 + '"}]}',
                     '{"version": 1, "macros": [{"slot": 0, "body": "' + "ff" * 200 + '"}]}',
                     '{"version": 1, "macros": [{"slot": 0, "body": "00"}]}',
                     '{"version": 1, "macros": [{"slot": 0, "body": "' + "00" * 200 + '", "addr": 1}]}'):
        with pytest.raises(plan.LightingRequestError):
            plan.parse_any_request(document)


def test_scroll_reads_and_requests():
    assert plan.scroll_from_read(bytes.fromhex("01000300050001003f00")) == (1, 3, 5, 1, 63)
    assert plan.scroll_from_read(bytes.fromhex("00000300050001003f00")) is None
    assert plan.scroll_from_read(bytes.fromhex("01010300050001003f00")) is None
    assert plan.parse_any_request(plan.scroll_request_document((1, 2, 3, 4, 63))) == ("scroll", (1, 2, 3, 4, 63))
    for bad in ((0, 1, 1, 1, 1), (1, 1, 1, 1, 64), (1, 1)):
        with pytest.raises(plan.LightingRequestError):
            plan.scroll_request_document(bad)
    with pytest.raises(plan.LightingRequestError):
        plan.parse_any_request('{"version": 1, "scroll": [1, 1, 1, 1, 1], "address": 32}')



def test_dpi_start_up_stage_plan():
    assert [plan.dpi_stage_select_address(p) for p in range(1, 6)] == [0x42, 0x102, 0x1B2, 0x262, 0x312]
    assert [plan.dpi_stage_select_from_read(bytes((s, 0))) for s in range(5)] == [0, 1, 2, 3, 4]
    assert all(plan.dpi_stage_select_from_read(data) is None
               for data in (b"\x05\x00", b"\x00\x01", b"\x00", b"\x00\x00\x00"))
    text = plan.dpi_stage_request_document([(1, 0), (5, 4)])
    assert plan.parse_any_request(text) == ("dpi_stage", ((1, 0), (5, 4)))
    for bad in ([(1, 5)], [(0, 1)], [(1, 1), (1, 2)], [], [(1, True)]):
        with pytest.raises(plan.LightingRequestError):
            plan.dpi_stage_request_document(bad)
