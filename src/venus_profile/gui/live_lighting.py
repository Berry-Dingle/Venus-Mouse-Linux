"""Keep the mouse in step with the app.

User decisions (``docs/LIVE_LIGHTING_PREVIEW_DESIGN.md`` §7, §11;
``docs/APPLY_HARDWARE_TESTS.md`` "Live vs Apply"), matching Windows:

* **Live** (sent as you change them): lighting, the active profile
  (selecting a profile in the app switches the mouse), and the 15 device macro
  slots (putting a library macro in a slot, removing, swapping or editing it
  uploads that slot, as Windows does). Buttons re-pointed by moving, swapping
  or removing a slot macro are sent live straight after the slot uploads, so
  no button plays the wrong macro (user decision, option b).
* **On Apply** (sent only when the user clicks Apply, for all five slots):
  polling, scroll lines (T6), DPI stage values, the DPI start-up stage (T5), button records.
  Edits stay pending when the user
  switches slots.

Rules:

* Nothing is sent until the mouse has been read successfully. The read gives
  the mouse's lighting per profile and its active profile; a profile whose
  lighting is not a known mode is never written this session.
* Any workspace change is compared with the mouse: a selected profile that is
  not the mouse's active profile is switched first, then lighting that
  differs; during an Apply, then polling (T3), scroll (T6), DPI stage values
  (T4), the DPI start-up stage (T5) and button records (T1) that differ.
* At most one helper run at a time; changes made meanwhile are coalesced and
  sent when it finishes.
* The mouse's own profile event after a switch from the app is an echo: it
  updates the known active profile but must not move the app's selection
  (the user may already have clicked another profile).
* Any failure stops live writing until the mouse is read again. No retry.

Pure logic: no widgets and no device access. ``runner(request, on_done)``
starts one helper run (``gui/lighting_preview.start_lighting_write``).
"""

from __future__ import annotations

import time
from typing import Callable

from ..lighting_preview_plan import (
    LIGHTING_READ_LENGTH, POLLING_ENUMS, PROFILE_COUNT, dpi_raw_from_record, dpi_request_document,
    dpi_targets_from_workspace, dpi_stage_request_document, dpi_stage_selects_from_workspace, lighting_is_known, polling_from_workspace, polling_request_document,
    profile_request_document, request_document, target_from_profile, button_record_is_known,
    button_records_equivalent, buttons_request_document, EMPTY_MACRO_BODY, FACTORY_SLOT_FILLER, MACRO_BODY_LENGTH,
    MACRO_SLOT_COUNT, macro_body_is_known, macro_slot_is_free, macros_request_document, SCROLL_MAX, SCROLL_MIN,
    scroll_from_workspace, scroll_request_document,
)
from .. import button_functions as bf
from ..apply_plan import button_records, report4_slot_report

DONE_STATUSES = ("ok", "unchanged")
#: How long after a switch from the app a matching profile event counts as its echo.
ECHO_WINDOW_SECONDS = 2.0


def lighting_from_decoded(lighting) -> bytes | None:
    """The 7 read bytes for a ``device_decode.DecodedLighting`` (None if unknown)."""
    if lighting is None:
        return None
    value = bytes(lighting.rgb) + bytes((lighting.mode_byte, lighting.companion, lighting.trailing,
                                         lighting.brightness & 0xFF))
    return value if lighting_is_known(value) else None


def dpi_from_decoded(profiles) -> dict[tuple[int, int], int]:
    """{(profile, stage): raw} for stage records read from the mouse that are known and enabled."""
    known = {}
    for profile in profiles:
        for stage, item in enumerate(profile.stages, start=1):
            raw = dpi_raw_from_record(item.record) if item is not None else None
            if raw is not None:
                known[(profile.number, stage)] = raw
    return known


def buttons_from_decoded(profiles) -> dict[tuple[int, int], bytes]:
    """{(profile, button): record} for button records read from the mouse with a known shape."""
    known = {}
    for profile in profiles:
        for button, record in enumerate(profile.buttons, start=1):
            if record is not None and button_record_is_known(record):
                known[(profile.number, button)] = bytes(record)
    return known


class LiveLighting:
    def __init__(self, workspace, runner: Callable, on_failure: Callable[[dict], None],
                 on_profile: Callable[[int], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 on_result: Callable[[str, dict], None] | None = None,
                 on_applied: Callable[[], None] | None = None,
                 on_live_buttons: Callable[[set], None] | None = None) -> None:
        self._workspace = workspace
        self._runner = runner
        self._on_failure = on_failure
        self._on_profile = on_profile or (lambda _profile: None)
        self._on_result = on_result or (lambda _kind, _document: None)
        self._on_applied = on_applied or (lambda: None)
        self._on_live_buttons = on_live_buttons or (lambda _keys: None)
        self._live_buttons: set[tuple[int, int]] = set()     # re-pointed by a slot move: send live
        self._applying = False
        self._clock = clock
        self._mouse: dict[int, bytes | None] | None = None   # None: not live (no read / suspended / failed)
        self._mouse_profile: int | None = None               # the mouse's active profile, 1-5
        self._mouse_polling: tuple[int, ...] | None = None   # five polling enums; None: unknown, never sent
        self._mouse_dpi: dict[tuple[int, int], int] = {}     # known stage values; others never written
        self._mouse_dpi_stage: dict[int, int] = {}           # profile -> zero-based start-up stage (known only)
        self._mouse_buttons: dict[tuple[int, int], bytes] = {}   # known records; others never written
        self._mouse_macros: dict[int, bytes] = {}            # slot -> body read from the mouse (known only)
        self._mouse_scroll: tuple[int, ...] | None = None    # five scroll-line values; None: unknown, never sent
        self._running = None
        self._running_kind: str | None = None
        self._pending = False
        self._echo: tuple[int, float] | None = None          # (profile, deadline) after an app switch
        self._idle_callbacks: list[Callable[[], None]] = []
        self.writes_started = 0

    # ------------------------------------------------------------ state
    @property
    def live(self) -> bool:
        return self._mouse is not None

    @property
    def busy(self) -> bool:
        return self._running is not None

    @property
    def mouse_profile(self) -> int | None:
        return self._mouse_profile

    def mouse_read(self, lighting: dict[int, bytes | None], active_profile: int | None = None,
                   polling=None, dpi: dict[tuple[int, int], int] | None = None,
                   buttons: dict[tuple[int, int], bytes] | None = None,
                   macros=None, scroll=None, dpi_stage=None) -> None:
        """A successful read: start following the mouse from this state."""
        self._mouse = {profile: lighting.get(profile) for profile in range(1, PROFILE_COUNT + 1)}
        self._mouse_profile = active_profile
        polling = tuple(polling) if polling is not None else None
        self._mouse_polling = (polling if polling is not None and len(polling) == PROFILE_COUNT
                               and all(value in POLLING_ENUMS for value in polling) else None)
        self._mouse_dpi = dict(dpi or {})
        self._mouse_dpi_stage = {profile: stage for profile, stage in enumerate(dpi_stage or [], start=1)
                                 if profile <= PROFILE_COUNT and isinstance(stage, int) and 0 <= stage < 5}
        self._mouse_buttons = dict(buttons or {})
        self._mouse_macros = {slot: bytes(body) for slot, body in enumerate(macros or [])
                              if slot < MACRO_SLOT_COUNT and body is not None
                              and (macro_body_is_known(body) or bytes(body) == FACTORY_SLOT_FILLER)}
        scroll = tuple(scroll) if scroll is not None else None
        self._mouse_scroll = (scroll if scroll is not None and len(scroll) == PROFILE_COUNT
                              and all(SCROLL_MIN <= value <= SCROLL_MAX for value in scroll) else None)
        self._applying = False
        self._echo = None
        self.sync()

    def mouse_profile_event(self, profile: int) -> bool:
        """The mouse reported its profile; returns whether the app should follow it."""
        echo, self._echo = self._echo, None
        self._mouse_profile = profile
        if echo is not None and echo[0] == profile and self._clock() <= echo[1]:
            return False                                   # echo of our own switch
        return True

    def mouse_dpi_stage_event(self, profile: int, stage: int) -> None:
        """DPI± on the mouse selected ``stage`` (zero-based): the mouse is already there."""
        if profile in self._mouse_dpi_stage and 0 <= stage < 5:
            self._mouse_dpi_stage[profile] = stage

    def suspend(self) -> None:
        """Stop sending (a read is starting, or the mouse went away)."""
        self._mouse = None
        self._pending = False
        self._applying = False
        self._live_buttons.clear()

    def push_live_buttons(self, keys) -> None:
        """Buttons re-pointed by a slot move/swap/remove: send them live (option b)."""
        if self._mouse is not None:
            self._live_buttons.update(keys)

    # ------------------------------------------------------------ Apply
    @property
    def applying(self) -> bool:
        return self._applying

    def has_pending_apply(self) -> bool:
        """Whether polling, DPI or button settings differ from the mouse (Apply would send something)."""
        return bool(self.wanted_polling() is not None or self.wanted_scroll() is not None
                    or self.wanted_dpi() or self.wanted_dpi_stage() or self.wanted_buttons())

    def apply(self) -> bool:
        """Send every pending Apply-category change (all five slots). False if nothing to do."""
        if self._mouse is None or not self.has_pending_apply():
            return False
        self._applying = True
        self.sync()
        return True

    def when_idle(self, callback: Callable[[], None]) -> None:
        if self.busy:
            self._idle_callbacks.append(callback)
        else:
            callback()

    # ------------------------------------------------------------ syncing
    def wanted_profile(self) -> int | None:
        if self._mouse is None or self._mouse_profile is None:   # unknown: never switch blindly
            return None
        selected = self._workspace.selected_slot
        return selected if selected != self._mouse_profile else None

    def wanted_polling(self) -> tuple[int, ...] | None:
        if self._mouse is None or self._mouse_polling is None:   # unknown: never sent
            return None
        values = polling_from_workspace(self._workspace)
        return values if values is not None and values != self._mouse_polling else None

    def wanted_dpi(self) -> list[tuple[int, int, int]]:
        if self._mouse is None:
            return []
        targets = dpi_targets_from_workspace(self._workspace)
        return [(profile, stage, raw) for (profile, stage), raw in sorted(targets.items())
                if (profile, stage) in self._mouse_dpi and self._mouse_dpi[(profile, stage)] != raw]

    def wanted_dpi_stage(self) -> list[tuple[int, int]]:
        """(profile, zero-based stage) whose start-up stage differs from the mouse (known profiles only)."""
        if self._mouse is None:
            return []
        return [(profile, stage) for profile, stage in sorted(dpi_stage_selects_from_workspace(self._workspace).items())
                if profile in self._mouse_dpi_stage and self._mouse_dpi_stage[profile] != stage]

    def wanted_scroll(self) -> tuple[int, ...] | None:
        if self._mouse is None or self._mouse_scroll is None:   # unknown: never sent
            return None
        values = scroll_from_workspace(self._workspace)
        return values if values is not None and values != self._mouse_scroll else None

    def wanted_buttons(self) -> list[tuple[int, int, bytes]]:
        if self._mouse is None or not self._mouse_buttons:
            return []
        wanted = []
        for key, record in sorted(button_records(self._workspace).items()):
            mouse = self._mouse_buttons.get(key)
            if mouse is None or record is None or not button_record_is_known(record):
                continue                     # unknown on the mouse, or not derivable yet (e.g. empty macro slot)
            if not button_records_equivalent(mouse, record) or self._fire_count_differs(key, mouse, record):
                wanted.append((key[0], key[1], record))
        return wanted

    def _fire_count_differs(self, key, mouse: bytes, record: bytes) -> bool:
        """A fire key whose count this profile stores must match the mouse's count too.

        Profiles without a stored count (Windows files) leave the mouse's count alone.
        """
        if record[0] != 0x99 or mouse[0] != 0x99 or record[2] == mouse[2]:
            return False
        fire = bf.read_assignment(self._workspace.profile(key[0]), key[1]).fire
        return fire is not None and fire.count is not None

    def desired_macro_body(self, slot: int) -> bytes | None:
        """What the app wants in a device slot, or None if it cannot be encoded (never sent)."""
        library = self._workspace.library
        macro_id = library.bank.slots[slot]
        if macro_id is not None:
            try:
                body = report4_slot_report(library.entry(macro_id), slot)[8:8 + MACRO_BODY_LENGTH]
            except (ValueError, KeyError, AttributeError):
                return None
            return body if macro_body_is_known(body) else None
        if slot in library.mouse_only:
            return bytes(library.mouse_only[slot])        # a macro only on the mouse: leave it
        return EMPTY_MACRO_BODY

    def wanted_macros(self) -> list[tuple[int, bytes]]:
        if self._mouse is None:
            return []
        wanted = []
        for slot, current in sorted(self._mouse_macros.items()):
            desired = self.desired_macro_body(slot)
            if desired is None or desired == current or macro_slot_is_free(desired) and macro_slot_is_free(current):
                continue                                  # factory filler already counts as empty
            wanted.append((slot, desired))
        return wanted

    def changed_targets(self) -> list:
        if self._mouse is None:
            return []
        targets = []
        for profile in range(1, PROFILE_COUNT + 1):
            current = self._mouse[profile]
            if current is None:
                continue
            target = target_from_profile(profile, self._workspace.profile(profile))
            if target is not None and target.lighting_bytes() != current:
                targets.append(target)
        return targets

    def sync(self) -> None:
        if self._mouse is None:
            return
        if self.busy:
            self._pending = True
            return
        profile = self.wanted_profile()
        if profile is not None:
            self._start("profile", profile_request_document(profile))
            return
        targets = self.changed_targets()
        if targets:
            self._start("lighting", request_document(targets))
            return
        macros = self.wanted_macros()
        if macros:
            self._start("macros", macros_request_document(macros))
            return
        if self._live_buttons:
            wanted = [item for item in self.wanted_buttons() if (item[0], item[1]) in self._live_buttons]
            if wanted:
                self._start("live-buttons", buttons_request_document(wanted))
                return
            done, self._live_buttons = self._live_buttons, set()   # nothing left to send for them
            self._on_live_buttons(done)
        if not self._applying:
            return
        polling = self.wanted_polling()
        if polling is not None:
            self._start("polling", polling_request_document(polling))
            return
        scroll = self.wanted_scroll()
        if scroll is not None:
            self._start("scroll", scroll_request_document(scroll))
            return
        stages = self.wanted_dpi()
        if stages:
            self._start("dpi", dpi_request_document(stages))
            return
        selects = self.wanted_dpi_stage()
        if selects:
            self._start("dpi_stage", dpi_stage_request_document(selects))
            return
        buttons = self.wanted_buttons()
        if buttons:
            self._start("buttons", buttons_request_document(buttons))
            return
        self._applying = False
        self._on_applied()

    def _start(self, kind: str, request: str) -> None:
        self.writes_started += 1
        self._running_kind = kind
        token = self._running = object()
        handle = self._runner(request, self._done)
        if self._running is token:          # not already finished synchronously
            self._running = handle

    def _done(self, document: dict) -> None:
        kind, self._running, self._running_kind = self._running_kind, None, None
        self._on_result(kind or "?", document)
        status = document.get("status")
        if status in DONE_STATUSES:
            if self._mouse is not None:
                if kind == "profile":
                    self._profile_switched(document.get("active_profile"))
                elif kind == "polling":
                    self._polling_set(document.get("polling"))
                elif kind == "scroll":
                    values = document.get("scroll")
                    if isinstance(values, list) and len(values) == PROFILE_COUNT \
                            and all(isinstance(v, int) and SCROLL_MIN <= v <= SCROLL_MAX for v in values):
                        self._mouse_scroll = tuple(values)
                elif kind == "dpi":
                    self._dpi_set(document.get("dpi"))
                elif kind == "dpi_stage":
                    self._dpi_stage_set(document.get("dpi_stage"))
                elif kind in ("buttons", "live-buttons"):
                    self._buttons_set(document.get("buttons"))
                elif kind == "macros":
                    self._macros_set(document.get("macros"))
                else:
                    self._update_from(document.get("records"))
            self._pending = False
            self.sync()                     # diff-based: sends only what still differs
        else:
            self._mouse = None
            self._pending = False
            self._applying = False
            self._live_buttons.clear()
            self._on_failure(document)
        if not self.busy:
            callbacks, self._idle_callbacks = self._idle_callbacks, []
            for callback in callbacks:
                callback()

    def _macros_set(self, entries) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            try:
                slot, body = int(entry["slot"]), bytes.fromhex(entry["body"])
            except (KeyError, TypeError, ValueError):
                continue
            if slot in self._mouse_macros and macro_body_is_known(body):
                self._mouse_macros[slot] = body

    def _buttons_set(self, entries) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            try:
                key, record = (int(entry["profile"]), int(entry["button"])), bytes.fromhex(entry["record"])
            except (KeyError, TypeError, ValueError):
                continue
            if button_record_is_known(record):
                self._mouse_buttons[key] = record

    def _dpi_set(self, entries) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            try:
                key, raw = (int(entry["profile"]), int(entry["stage"])), int(entry["raw"])
            except (KeyError, TypeError, ValueError):
                continue
            if key in self._mouse_dpi:
                self._mouse_dpi[key] = raw

    def _dpi_stage_set(self, entries) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            try:
                profile, stage = int(entry["profile"]), int(entry["stage"])
            except (KeyError, TypeError, ValueError):
                continue
            if profile in self._mouse_dpi_stage and 0 <= stage < 5:
                self._mouse_dpi_stage[profile] = stage

    def _polling_set(self, values) -> None:
        if isinstance(values, list) and len(values) == PROFILE_COUNT and all(v in POLLING_ENUMS for v in values):
            self._mouse_polling = tuple(values)

    def _profile_switched(self, profile) -> None:
        if not isinstance(profile, int) or not 1 <= profile <= PROFILE_COUNT:
            return
        self._mouse_profile = profile
        self._echo = (profile, self._clock() + ECHO_WINDOW_SECONDS)
        self._on_profile(profile)

    def _update_from(self, records) -> None:
        if not isinstance(records, dict):
            return
        for profile in range(1, PROFILE_COUNT + 1):
            text = records.get(str(profile))
            try:
                value = bytes.fromhex(text) if isinstance(text, str) else None
            except ValueError:
                value = None
            if value is None or len(value) != LIGHTING_READ_LENGTH:
                continue
            self._mouse[profile] = value if lighting_is_known(value) else None
