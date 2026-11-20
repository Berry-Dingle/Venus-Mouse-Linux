"""Pure encoding for live lighting writes (no device access, no report bytes).

Shared by the GUI (to decide *whether* lighting differs from the mouse) and by
the one-shot helper ``venus_profile.preview_lighting`` (which builds and sends
the reports). See ``docs/LIVE_LIGHTING_PREVIEW_DESIGN.md``.

Each profile's lighting on the mouse is 7 bytes read by the captured read plan
at ``0x0449 + 8*(profile-1)``: ``RR GG BB`` (direct RGB), a 3-byte mode suffix,
and a brightness byte. Only suffixes with saved evidence are allowed:

=================  ============  =============================================
Mode               Suffix        Evidence
=================  ============  =============================================
full-brightness    01 00 01      Linux live writes, persisted
breathing          02 ss 01      Linux live cycle, ss in {08, 05, 02, 00}
wave               03 00 01      Linux live cycle (visual)
flash              04 05 01      Linux live cycle (visual)
rainbow            02 00 03      Windows capture + saved reads
off                00 00 00      Windows capture + saved reads (RGB kept)
=================  ============  =============================================

Brightness is 0..3. Anything else is not encodable, and the GUI then leaves
that profile alone.

The same helper also switches the mouse's active profile: one byte at
``0x002C``, zero-based (``hid.exe`` 0x40eef0 reads it, 0x410140 writes it;
``investigation/LIVE_EVENT_FINDINGS.md`` §10; the read side is
live-confirmed by the user). The request carries only the profile number.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

PROFILE_COUNT = 5
RECORD_LENGTH = 6                 # RGB + mode suffix
LIGHTING_READ_LENGTH = 7          # record + brightness, as in the read plan
BRIGHTNESS_OFFSET = 6
RECORD_ADDRESSES = (0x0449, 0x0451, 0x0459, 0x0461, 0x0469)
BRIGHTNESS_ADDRESSES = tuple(address + BRIGHTNESS_OFFSET for address in RECORD_ADDRESSES)
ACTIVE_PROFILE_ADDRESS = 0x002C   # zero-based active profile, 1-byte read in the read plan
REQUEST_VERSION = 1
MAX_REQUEST_BYTES = 4096

BREATHING_SPEEDS = (8, 5, 2, 0)   # Low, Medium, High, "Full Brightness" UI label
FLASH_COMPANION = 5
BRIGHTNESS_LEVELS = (0, 1, 2, 3)

MODES = ("full-brightness", "breathing", "wave", "flash", "rainbow", "off")
#: PFD ``LedMode1`` value -> mode name (``pfd.LedMode``).
PFD_MODE_NAMES = {1: "full-brightness", 0: "breathing", 3: "wave", 4: "flash", 2: "rainbow", 5: "off"}

_FIXED_SUFFIXES = {
    "full-brightness": bytes((0x01, 0x00, 0x01)),
    "wave": bytes((0x03, 0x00, 0x01)),
    "flash": bytes((0x04, FLASH_COMPANION, 0x01)),
    "rainbow": bytes((0x02, 0x00, 0x03)),
    "off": bytes((0x00, 0x00, 0x00)),
}
ALLOWED_SUFFIXES = frozenset(_FIXED_SUFFIXES.values()) | frozenset(
    bytes((0x02, speed, 0x01)) for speed in BREATHING_SPEEDS)


class LightingRequestError(ValueError):
    """A lighting request is malformed or outside the evidenced values."""


@dataclass(frozen=True, slots=True)
class LightingTarget:
    profile: int
    rgb: tuple[int, int, int]
    mode: str
    brightness: int
    speed: int | None = None      # breathing only

    def __post_init__(self) -> None:
        _check_int(self.profile, range(1, PROFILE_COUNT + 1), "profile")
        if not isinstance(self.rgb, tuple) or len(self.rgb) != 3:
            raise LightingRequestError("rgb must be three channels")
        for channel in self.rgb:
            _check_int(channel, range(256), "rgb channel")
        if self.mode not in MODES:
            raise LightingRequestError(f"unknown lighting mode {self.mode!r}")
        _check_int(self.brightness, BRIGHTNESS_LEVELS, "brightness")
        if self.mode == "breathing":
            _check_int(self.speed, BREATHING_SPEEDS, "breathing speed")
        elif self.speed is not None:
            raise LightingRequestError("speed is only valid for breathing")

    def record(self) -> bytes:
        """The 6 bytes at the profile's record address."""
        suffix = (bytes((0x02, self.speed, 0x01)) if self.mode == "breathing"
                  else _FIXED_SUFFIXES[self.mode])
        return bytes(self.rgb) + suffix

    def lighting_bytes(self) -> bytes:
        """The 7 bytes the read plan returns for this profile."""
        return self.record() + bytes((self.brightness,))

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {"profile": self.profile, "rgb": list(self.rgb),
                                       "mode": self.mode, "brightness": self.brightness}
        if self.speed is not None:
            document["speed"] = self.speed
        return document


def _check_int(value, allowed, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value not in allowed:
        raise LightingRequestError(f"{name} {value!r} is not allowed")


def record_address(profile: int) -> int:
    _check_int(profile, range(1, PROFILE_COUNT + 1), "profile")
    return RECORD_ADDRESSES[profile - 1]


def suffix_is_known(suffix: bytes) -> bool:
    return bytes(suffix) in ALLOWED_SUFFIXES


def lighting_is_known(lighting: bytes | None) -> bool:
    """True if 7 read bytes hold an evidenced mode suffix and brightness."""
    return (lighting is not None and len(lighting) == LIGHTING_READ_LENGTH
            and suffix_is_known(lighting[3:6]) and lighting[6] in BRIGHTNESS_LEVELS)


def target_from_profile(number: int, profile) -> LightingTarget | None:
    """The lighting a workspace profile asks for, or None if not encodable."""
    try:
        mode = PFD_MODE_NAMES.get(int(profile.led_mode_raw))
        if mode is None:
            return None
        if mode == "flash" and int(profile.flash_state_raw) != FLASH_COMPANION:
            return None
        speed = int(profile.breathing_speed_raw) if mode == "breathing" else None
        return LightingTarget(number, tuple(int(c) for c in profile.led_color_rgb), mode,
                              int(profile.brightness_raw), speed)
    except (LightingRequestError, ValueError, TypeError):
        return None


def request_document(targets) -> str:
    """The JSON line the GUI sends to the helper on stdin."""
    targets = tuple(targets)
    if not targets:
        raise LightingRequestError("no lighting to send")
    profiles = [target.profile for target in targets]
    if len(set(profiles)) != len(profiles):
        raise LightingRequestError("each profile may appear once")
    return json.dumps({"version": REQUEST_VERSION, "profiles": [t.to_dict() for t in targets]})


_TARGET_KEYS = {"profile", "rgb", "mode", "brightness", "speed"}


def parse_request(text: str) -> tuple[LightingTarget, ...]:
    """Strictly parse the helper's stdin document (semantic values only)."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise LightingRequestError("request is missing or too large")
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise LightingRequestError("request is not JSON") from exc
    if not isinstance(document, dict) or set(document) != {"version", "profiles"}:
        raise LightingRequestError("request must contain exactly version and profiles")
    if document["version"] != REQUEST_VERSION:
        raise LightingRequestError("unsupported request version")
    entries = document["profiles"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= PROFILE_COUNT:
        raise LightingRequestError("profiles must list 1 to 5 entries")
    targets = []
    for entry in entries:
        if not isinstance(entry, dict) or not set(entry) <= _TARGET_KEYS \
                or not {"profile", "rgb", "mode", "brightness"} <= set(entry):
            raise LightingRequestError("each profile entry must hold profile, rgb, mode, brightness [, speed]")
        rgb = entry["rgb"]
        if not isinstance(rgb, list):
            raise LightingRequestError("rgb must be a list")
        targets.append(LightingTarget(entry["profile"], tuple(rgb), entry["mode"], entry["brightness"],
                                      entry.get("speed")))
    if len({t.profile for t in targets}) != len(targets):
        raise LightingRequestError("each profile may appear once")
    return tuple(targets)


# ----------------------------------------------------------------- active profile

def profile_request_document(profile: int) -> str:
    """The JSON line asking the helper to make ``profile`` (1-5) active on the mouse."""
    _check_int(profile, range(1, PROFILE_COUNT + 1), "profile")
    return json.dumps({"version": REQUEST_VERSION, "active_profile": profile})


def parse_any_request(text: str):
    """``("lighting", …)``, ``("profile", 1-5)``, ``("polling", …)``, ``("dpi", …)``, ``("dpi_stage", …)``,
    ``("buttons", …)``, ``("macros", …)`` or ``("scroll", …)``; strict."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise LightingRequestError("request is missing or too large")
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise LightingRequestError("request is not JSON") from exc
    if isinstance(document, dict) and set(document) == {"version", "scroll"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        values = document["scroll"]
        if not isinstance(values, list):
            raise LightingRequestError("scroll must be a list")
        scroll_request_document(values)              # validates count and range
        return "scroll", tuple(values)
    if isinstance(document, dict) and set(document) == {"version", "macros"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        return "macros", _parse_macros(document["macros"])
    if isinstance(document, dict) and set(document) == {"version", "buttons"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        return "buttons", _parse_buttons(document["buttons"])
    if isinstance(document, dict) and set(document) == {"version", "dpi"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        return "dpi", _parse_dpi(document["dpi"])
    if isinstance(document, dict) and set(document) == {"version", "dpi_stage"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        return "dpi_stage", _parse_dpi_stage(document["dpi_stage"])
    if isinstance(document, dict) and set(document) == {"version", "polling"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        values = document["polling"]
        if not isinstance(values, list):
            raise LightingRequestError("polling must be a list")
        polling_request_document(values)            # validates count and enums
        return "polling", tuple(values)
    if isinstance(document, dict) and set(document) == {"version", "active_profile"}:
        if document["version"] != REQUEST_VERSION:
            raise LightingRequestError("unsupported request version")
        _check_int(document["active_profile"], range(1, PROFILE_COUNT + 1), "active profile")
        return "profile", document["active_profile"]
    return "lighting", parse_request(text)



# ----------------------------------------------------------------- polling

#: Polling enum per profile at 0x0032 + 2*(p-1), each followed by a 00 companion
#: (read plan: 0x0032/5 and 0x0038/3). hid.exe 0x410230 writes 0x0032/6 and
#: 0x0038/4 then F1 02 08. 8/4/2/1 = 125/250/500/1000 Hz (pfd.POLLING_ENUM_TO_HZ).
POLLING_WRITE_ADDRESSES = (0x0032, 0x0038)
POLLING_READS = ((0x0032, 5), (0x0038, 3))
POLLING_ENUMS = (8, 4, 2, 1)


def polling_from_reads(first: bytes, second: bytes) -> tuple[int, ...] | None:
    """The five polling enums from the two read-plan reads, or None if unknown.

    Known means every value is an evidenced enum and every companion byte that
    was read (0x0033, 0x0035, 0x0039) is 00.
    """
    if first is None or second is None or len(first) != 5 or len(second) != 3:
        return None
    values = (first[0], first[2], first[4], second[0], second[2])
    if any(value not in POLLING_ENUMS for value in values) or first[1] or first[3] or second[1]:
        return None
    return values


def polling_from_workspace(workspace) -> tuple[int, ...] | None:
    try:
        values = tuple(int(workspace.profile(p).polling_rate_raw) for p in range(1, PROFILE_COUNT + 1))
    except (ValueError, TypeError):
        return None
    return values if all(value in POLLING_ENUMS for value in values) else None


def polling_request_document(values) -> str:
    values = tuple(values)
    if len(values) != PROFILE_COUNT:
        raise LightingRequestError("polling needs five values")
    for value in values:
        _check_int(value, POLLING_ENUMS, "polling value")
    return json.dumps({"version": REQUEST_VERSION, "polling": list(values)})


# ----------------------------------------------------------------- DPI stage values

#: Per-profile DPI block (apply_plan.DPI_PROFILE_BASES). The read plan reads
#: base+2 for 32 bytes (start-up stage byte, then the five stage records);
#: stage s (1-5) record = 5 bytes at base+4+6*(s-1): enable, lookup, selector, 00, 00.
DPI_PROFILE_BASES = (0x0040, 0x0100, 0x01B0, 0x0260, 0x0310)
DPI_READ_OFFSET = 2
DPI_READ_LENGTH = 32
DPI_RECORD_LENGTH = 5
DPI_MAX_RAW = 106
#: Copy of apply_plan._DPI_LOOKUP (hid.exe 0x47E3F4..0x47E5B8); tests keep them identical.
DPI_LOOKUP = (0, 4, 6, 9, 11, 13, 15, 18, 20, 22, 24, 27, 29, 31, 33, 36, 38, 40, 43, 45, 47, 49, 52, 54, 56, 58, 61, 63, 65, 67, 70, 72, 74, 77, 79, 81, 83, 86, 88, 90, 92, 95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117, 120, 122, 124, 127, 129, 131, 133, 135, 138, 140, 72, 74, 77, 79, 81, 83, 86, 88, 90, 92, 95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117, 120, 122, 124, 127, 129, 131, 133, 135, 138, 140, 95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117, 120, 122, 124)


def dpi_record_address(profile: int, stage: int) -> int:
    _check_int(profile, range(1, PROFILE_COUNT + 1), "profile")
    _check_int(stage, range(1, 6), "DPI stage")
    return DPI_PROFILE_BASES[profile - 1] + 4 + 6 * (stage - 1)


def dpi_record(raw: int) -> bytes:
    """Enabled stage record for a raw sensor step (enable is always 01 in this version)."""
    _check_int(raw, range(DPI_MAX_RAW + 1), "DPI raw step")
    selector = 0 if raw <= 0x3D else (1 if raw <= 0x5C else 4)
    return bytes((1, DPI_LOOKUP[raw], selector, 0, 0))


_DPI_RECORD_TO_RAW = {dpi_record(raw): raw for raw in range(DPI_MAX_RAW + 1)}


def dpi_raw_from_record(record: bytes) -> int | None:
    """Raw step of an enabled, evidenced stage record, else None (unknown: never written over)."""
    return _DPI_RECORD_TO_RAW.get(bytes(record))


def dpi_targets_from_workspace(workspace) -> dict[tuple[int, int], int]:
    """{(profile, stage): raw} for every stage the app can encode (enabled, X == Y, in range)."""
    targets = {}
    for profile in range(1, PROFILE_COUNT + 1):
        settings = workspace.profile(profile)
        for stage in range(1, 6):
            try:
                value = settings.dpi_stage_value(stage)
            except ValueError:
                continue
            if value.enabled == 1 and value.x_raw == value.y_raw and 0 <= value.x_raw <= DPI_MAX_RAW:
                targets[(profile, stage)] = value.x_raw
    return targets


def dpi_request_document(stages) -> str:
    """stages: iterable of (profile, stage, raw)."""
    entries = []
    seen = set()
    for profile, stage, raw in stages:
        dpi_record_address(profile, stage)
        dpi_record(raw)
        if (profile, stage) in seen:
            raise LightingRequestError("each DPI stage may appear once")
        seen.add((profile, stage))
        entries.append({"profile": profile, "stage": stage, "raw": raw})
    if not entries:
        raise LightingRequestError("no DPI stages to send")
    return json.dumps({"version": REQUEST_VERSION, "dpi": entries})


def _parse_dpi(entries) -> tuple[tuple[int, int, int], ...]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= PROFILE_COUNT * 5:
        raise LightingRequestError("dpi must list 1 to 25 stages")
    stages = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"profile", "stage", "raw"}:
            raise LightingRequestError("each DPI entry must hold exactly profile, stage, raw")
        stages.append((entry["profile"], entry["stage"], entry["raw"]))
    dpi_request_document(stages)                 # validates ranges and duplicates
    return tuple(stages)


# ----------------------------------------------------------------- DPI start-up stage (T5)

#: The profile's selected (start-up) DPI stage, zero-based 0-4, at base+2;
#: base+3 is 00 (docs/DPI_STAGE.md; read-plan block base+2/32). hid.exe
#: 0x40FC60 writes it as a 2-byte F3 [stage, 00] per profile, then F1 02 02
#: and F1 02 10 (static; Apply capture led_mode_breathing_to_full).
DPI_STAGE_SELECT_OFFSET = 2
DPI_STAGE_SELECT_LENGTH = 2
DPI_STAGE_COUNT = 5


def dpi_stage_select_address(profile: int) -> int:
    _check_int(profile, range(1, PROFILE_COUNT + 1), "profile")
    return DPI_PROFILE_BASES[profile - 1] + DPI_STAGE_SELECT_OFFSET


def dpi_stage_select_from_read(data: bytes) -> int | None:
    """Zero-based stage from the 2 bytes at base+2, or None if not [0-4, 00] (never written over)."""
    data = bytes(data)
    if len(data) != DPI_STAGE_SELECT_LENGTH or data[1] != 0 or data[0] >= DPI_STAGE_COUNT:
        return None
    return data[0]


def dpi_stage_selects_from_workspace(workspace) -> dict[int, int]:
    """{profile: zero-based start-up stage} for every profile whose stored stage is 0-4."""
    selects = {}
    for profile in range(1, PROFILE_COUNT + 1):
        raw = workspace.profile(profile).selected_dpi_stage_raw
        if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw < DPI_STAGE_COUNT:
            selects[profile] = raw
    return selects


def dpi_stage_request_document(entries) -> str:
    """entries: iterable of (profile 1-5, zero-based stage 0-4)."""
    items, seen = [], set()
    for profile, stage in entries:
        dpi_stage_select_address(profile)
        _check_int(stage, range(DPI_STAGE_COUNT), "DPI start-up stage")
        if profile in seen:
            raise LightingRequestError("each profile may appear once")
        seen.add(profile)
        items.append({"profile": profile, "stage": stage})
    if not items:
        raise LightingRequestError("no DPI start-up stages to send")
    return json.dumps({"version": REQUEST_VERSION, "dpi_stage": items})


def _parse_dpi_stage(entries) -> tuple[tuple[int, int], ...]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= PROFILE_COUNT:
        raise LightingRequestError("dpi_stage must list 1 to 5 profiles")
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"profile", "stage"}:
            raise LightingRequestError("each dpi_stage entry must hold exactly profile and stage")
        parsed.append((entry["profile"], entry["stage"]))
    dpi_stage_request_document(parsed)          # validates ranges and duplicates
    return tuple(parsed)


# ----------------------------------------------------------------- button records

#: Per-profile button record bases and the 20 record offsets (button_assignments).
BUTTON_PROFILE_BASES = (0x0082, 0x0142, 0x01F2, 0x02A2, 0x0352)
BUTTON_OFFSETS = tuple(range(0, 18 * 4, 4)) + (0x58, 0x5C)
BUTTON_COUNT = 20
BUTTON_RECORD_LENGTH = 4
MACRO_SLOT_COUNT = 15
#: Fixed records the decoder names (device_decode._NAMED_TEMPLATES); tests keep them identical.
NAMED_BUTTON_RECORDS = frozenset({bytes.fromhex("00000000"), bytes.fromhex("81000000"), bytes.fromhex("82000000"), bytes.fromhex("83000000"), bytes.fromhex("84000000"), bytes.fromhex("85000000"), bytes.fromhex("88000000"), bytes.fromhex("89000000"), bytes.fromhex("8a000000"), bytes.fromhex("8b000000"), bytes.fromhex("8c000000"), bytes.fromhex("8d000000"), bytes.fromhex("8e01b500"), bytes.fromhex("8e01b600"), bytes.fromhex("8e01b700"), bytes.fromhex("8e01cd00"), bytes.fromhex("8e01e200"), bytes.fromhex("8e01e900"), bytes.fromhex("8e01ea00"), bytes.fromhex("8e01ff1f"), bytes.fromhex("8e01ff20"), bytes.fromhex("8e01ff21"), bytes.fromhex("8e01ff22"), bytes.fromhex("8e01ff23"), bytes.fromhex("8e01ff24"), bytes.fromhex("8e01ff25"), bytes.fromhex("8e01ff26"), bytes.fromhex("8e01ff3a"), bytes.fromhex("8f010400"), bytes.fromhex("8f010600"), bytes.fromhex("8f010900"), bytes.fromhex("8f011100"), bytes.fromhex("8f011300"), bytes.fromhex("8f011600"), bytes.fromhex("8f011900"), bytes.fromhex("8f011b00"), bytes.fromhex("8f042b00"), bytes.fromhex("8f043d00"), bytes.fromhex("8f080700"), bytes.fromhex("8f080800"), bytes.fromhex("8f080f00"), bytes.fromhex("8f081500"), bytes.fromhex("94000000"), bytes.fromhex("95000000"), bytes.fromhex("97000000"), bytes.fromhex("98000000"), bytes.fromhex("9b040000")})
FIRE_TRIGGERS = frozenset({0x81, 0x82, 0x84})   # left, right, middle; keyboard triggers use a HID usage


def button_record_address(profile: int, button: int) -> int:
    _check_int(profile, range(1, PROFILE_COUNT + 1), "profile")
    _check_int(button, range(1, BUTTON_COUNT + 1), "button")
    return BUTTON_PROFILE_BASES[profile - 1] + BUTTON_OFFSETS[button - 1]


def button_record_is_known(record: bytes) -> bool:
    """True for the record shapes the decoder recognises (device_decode._apply_button)."""
    record = bytes(record)
    if len(record) != BUTTON_RECORD_LENGTH:
        return False
    code, b1, b2, b3 = record
    if record in NAMED_BUTTON_RECORDS:
        return True
    if code == 0x90:                                   # single key: 90 00 usage 00
        return b1 == 0 and b2 != 0 and b3 == 0
    if code == 0x8F:                                   # combo: 8F modifiers usage 00
        return b1 <= 0x0F and b3 == 0
    if code == 0x99:                                   # fire key: 99 trigger count delay
        return (b1 in FIRE_TRIGGERS or 0x04 <= b1 <= 0xE7) and b2 >= 1
    if code == 0x91:                                   # macro: 91 flags|slot count(le16)
        return (b1 & 0x3F) < MACRO_SLOT_COUNT and (b1 & 0xC0) in (0x00, 0x40, 0x80)
    return False


def button_records_equivalent(mouse: bytes, app: bytes) -> bool:
    """Whether sending app would change nothing the profile can express.

    Fire count is not stored in profile files (the mouse keeps its own), and a
    macro's loop flags/count belong to the macro (Stage 3), so those compare by
    trigger/delay and by slot respectively.
    """
    mouse, app = bytes(mouse), bytes(app)
    if mouse == app:
        return True
    if mouse[0] == app[0] == 0x99:
        return mouse[1] == app[1] and mouse[3] == app[3]
    if mouse[0] == app[0] == 0x91:
        return (mouse[1] & 0x3F) == (app[1] & 0x3F)
    return False


def buttons_request_document(entries) -> str:
    """entries: iterable of (profile, button, record bytes)."""
    items, seen = [], set()
    for profile, button, record in entries:
        button_record_address(profile, button)
        if not button_record_is_known(record):
            raise LightingRequestError(f"button record {bytes(record).hex(' ')} is not a known shape")
        if (profile, button) in seen:
            raise LightingRequestError("each button may appear once")
        seen.add((profile, button))
        items.append({"profile": profile, "button": button, "record": bytes(record).hex()})
    if not items:
        raise LightingRequestError("no button records to send")
    return json.dumps({"version": REQUEST_VERSION, "buttons": items})


def _parse_buttons(entries) -> tuple[tuple[int, int, bytes], ...]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= PROFILE_COUNT * BUTTON_COUNT:
        raise LightingRequestError("buttons must list 1 to 100 records")
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"profile", "button", "record"} \
                or not isinstance(entry["record"], str) or len(entry["record"]) != 8:
            raise LightingRequestError("each button entry must hold exactly profile, button, record (8 hex digits)")
        try:
            record = bytes.fromhex(entry["record"])
        except ValueError as exc:
            raise LightingRequestError("button record is not hex") from exc
        parsed.append((entry["profile"], entry["button"], record))
    buttons_request_document(parsed)             # validates addresses, shapes and duplicates
    return tuple(parsed)


# ----------------------------------------------------------------- macro slots (Stage 3)

#: Device macro slot s (0-14) body: 200 bytes at 0x0478 + 0xC8*s; the read plan
#: reads each slot as four 50-byte requests. Windows uploads one slot per
#: F5 00 / Report-4 / F5 01 bracket (hid.exe 0x4142b0 -> 0x412b50; captured
#: macro_slot1_a_press_release frame 4260: 04 F3 78 04 C8 00 00 00 + body + 48 x 00).
MACRO_SLOT_BASE = 0x0478
MACRO_SLOT_STRIDE = 0xC8
MACRO_BODY_LENGTH = 200
MACRO_RECORD_COUNT = 63                 # 63 x 3 = 189 action bytes, then 11 zero bytes
MACRO_READ_LENGTH = 50
MACRO_READS_PER_SLOT = 4
MACRO_REPORT_LENGTH = 256
EMPTY_MACRO_BODY = bytes(MACRO_BODY_LENGTH)   # factory empty slot
#: Factory filler in slot 8 (index 7) of a new mouse: exactly ff ff then 198 zeros.
#: Windows treats it as a free slot and records over it (APPLY_HARDWARE_TESTS.md
#: §5.1). It counts as free here; the app never writes it.
FACTORY_SLOT_FILLER = b"\xff\xff" + bytes(MACRO_BODY_LENGTH - 2)


def macro_slot_is_free(body: bytes) -> bool:
    """An empty slot: all zeros, or the exact factory filler."""
    return bytes(body) in (EMPTY_MACRO_BODY, FACTORY_SLOT_FILLER)


def macro_slot_address(slot: int) -> int:
    _check_int(slot, range(MACRO_SLOT_COUNT), "macro slot")
    return MACRO_SLOT_BASE + MACRO_SLOT_STRIDE * slot


def macro_body_is_known(body: bytes) -> bool:
    """A body made only of known action records (actions.py), zero padding after them."""
    body = bytes(body)
    if len(body) != MACRO_BODY_LENGTH or any(body[MACRO_RECORD_COUNT * 3:]):
        return False
    padding = False
    for index in range(MACRO_RECORD_COUNT):
        code, first, second = body[index * 3:index * 3 + 3]
        if (code, first, second) == (0, 0, 0):
            padding = True
            continue
        if padding:
            return False                    # an action after the padding started
        if code in (0x84, 0x04):            # keyboard down/up: usage, 00
            if first == 0 or second != 0:
                return False
        elif code in (0x81, 0x01):          # mouse button down/up: mask 1/2/4, 00
            if first not in (1, 2, 4) or second != 0:
                return False
        elif code != 0x06:                  # delay: 16-bit count of 10 ms
            return False
    return True


def macros_request_document(entries) -> str:
    """entries: iterable of (slot 0-14, 200-byte body)."""
    items, seen = [], set()
    for slot, body in entries:
        macro_slot_address(slot)
        if not macro_body_is_known(body):
            raise LightingRequestError(f"macro slot {slot + 1} body is not a known action list")
        if slot in seen:
            raise LightingRequestError("each macro slot may appear once")
        seen.add(slot)
        items.append({"slot": slot, "body": bytes(body).hex()})
    if not items:
        raise LightingRequestError("no macro slots to send")
    return json.dumps({"version": REQUEST_VERSION, "macros": items})


def _parse_macros(entries) -> tuple[tuple[int, bytes], ...]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= MACRO_SLOT_COUNT:
        raise LightingRequestError("macros must list 1 to 15 slots")
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"slot", "body"} or not isinstance(entry["body"], str) \
                or len(entry["body"]) != MACRO_BODY_LENGTH * 2:
            raise LightingRequestError("each macro entry must hold exactly slot and body (400 hex digits)")
        try:
            body = bytes.fromhex(entry["body"])
        except ValueError as exc:
            raise LightingRequestError("macro body is not hex") from exc
        parsed.append((entry["slot"], body))
    macros_request_document(parsed)          # validates slots, bodies and duplicates
    return tuple(parsed)


# ----------------------------------------------------------------- scroll lines (T6)

#: Wheel scroll lines, one byte + 00 per profile at 0x0020 (hid.exe 0x410070:
#: Report 3, 64 bytes, "03 F3 20 00 0A 00 00 00" + p1 00 ... p5 00 + zeros).
#: Windows never applies it to the PC (0x4109d0 sets only acceleration, pointer
#: speed and double-click time), so the mouse uses it. Range as the editor: 1-63.
SCROLL_ADDRESS = 0x0020
SCROLL_LENGTH = 10
SCROLL_REPORT_LENGTH = 64
SCROLL_MIN, SCROLL_MAX = 1, 63


def scroll_from_read(data: bytes | None) -> tuple[int, ...] | None:
    if data is None or len(data) != SCROLL_LENGTH or any(data[1::2]):
        return None
    values = tuple(data[0::2])
    return values if all(SCROLL_MIN <= value <= SCROLL_MAX for value in values) else None


def scroll_from_workspace(workspace) -> tuple[int, ...] | None:
    try:
        values = tuple(int(workspace.profile(p).scroll_speed_raw) for p in range(1, PROFILE_COUNT + 1))
    except (ValueError, TypeError):
        return None
    return values if all(SCROLL_MIN <= value <= SCROLL_MAX for value in values) else None


def scroll_request_document(values) -> str:
    values = tuple(values)
    if len(values) != PROFILE_COUNT:
        raise LightingRequestError("scroll needs five values")
    for value in values:
        _check_int(value, range(SCROLL_MIN, SCROLL_MAX + 1), "scroll lines")
    return json.dumps({"version": REQUEST_VERSION, "scroll": list(values)})
