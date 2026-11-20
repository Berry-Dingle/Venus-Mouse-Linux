"""Macro manager presentation state backed by the persistent library.

The library (unlimited, saved on disk) and the 15-slot device bank live in
:class:`venus_profile.macro_library.MacroLibrary`; button references live in
the five profiles.  Bank operations go through the workspace so that every
profile's ``KeyNumber``/``MacroName`` references stay consistent.  Nothing in
this module performs device access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import re

from ...actions import (
    ACTION_DELAY_MAX_MS,
    ACTION_DELAY_QUANTUM_MS,
    MOUSE_MASK_NAMES,
    ActionRecord,
    ActionType,
)
from ...hid_usage import KEY_NAMES, key_name, parse_key
from ...macro import ACTION_RECORD_CAPACITY, MacroFile
from ...macro_library import (
    DELAY_TYPE_DEFAULT,
    DELAY_TYPE_NONE,
    DELAY_TYPE_RECORDED,
    LibraryMacro,
    MacroLibraryError,
    MINIMUM_ACTION_DELAY_MS,
    effective_default_delay_ms,
    set_delay_settings,
    set_loop,
)
from ...validation import validate_macro

LOOP_LABELS = {
    0: "Repeat a fixed number of times",
    1: "Repeat while the button is held",
    2: "Run once",
    3: "Toggle: repeat until pressed again",
}
LOOP_COUNT_MAX = 255  # Windows editor: "LOOPS TIME(1-255)"


def format_keyboard_display(usage: int) -> str:
    return key_name(usage)


def format_keyboard_editor(usage: int) -> str:
    return KEY_NAMES.get(usage, f"0x{usage:02X}")


def format_action_display_value(record: ActionRecord) -> str:
    if record.action_type == ActionType.KEYBOARD:
        return format_keyboard_display(record.usage)
    if record.action_type == ActionType.MOUSE_BUTTON:
        return {1: "Left button", 2: "Right button", 4: "Middle button"}.get(record.usage, "Unknown mouse button")
    if record.action_type == ActionType.DELAY:
        return f"{record.delay_ms:,} ms"
    if record.action_type == ActionType.UNKNOWN:
        return "Raw record"
    if record.action_type == ActionType.ZERO:
        return "Padding"
    return "Unknown value"


def format_action_editor_value(record: ActionRecord) -> str:
    if record.action_type == ActionType.KEYBOARD:
        return format_keyboard_editor(record.usage)
    if record.action_type == ActionType.DELAY:
        return str(record.delay_ms)
    if record.action_type == ActionType.MOUSE_BUTTON:
        return {1: "Left button", 2: "Right button", 4: "Middle button"}.get(record.usage, "")
    return ""


def parse_keyboard_editor(text: str) -> int:
    return parse_key(text)


parse_keyboard_editor_value = parse_keyboard_editor


@dataclass(frozen=True, slots=True)
class MacroUsage:
    profile: int
    button: int
    description: str

    @property
    def stable_id(self) -> str:
        return f"profile:{self.profile - 1}/button:{self.button}"


@dataclass(frozen=True, slots=True)
class MacroActionPresentation:
    index: int
    record: ActionRecord
    action: str
    input_label: str
    delay_label: str
    warning: str | None = None
    stable_id: str = ""


@dataclass(frozen=True, slots=True)
class MacroImpact:
    """A pending bank operation that changes existing button references."""

    operation: str  # "replace", "unload", "swap" or "delete"
    slot_index: int
    macro_name: str
    replacement_name: str | None
    usage: tuple[MacroUsage, ...]
    replacement_id: str | None = None
    delete_macro_id: str | None = None
    other_slot: int | None = None

    @property
    def message(self) -> str:
        affected = "; ".join(item.description for item in self.usage)
        if self.operation == "replace":
            return (f"Loading {self.replacement_name} into Slot {self.slot_index + 1} changes "
                    f"{len(self.usage)} button(s) from {self.macro_name}: {affected}")
        if self.operation == "swap":
            return (f"Swapping Slot {self.slot_index + 1} and Slot {(self.other_slot or 0) + 1}; "
                    f"buttons keep their macros and are renumbered: {affected}")
        if self.operation == "delete":
            return f"Deleting {self.macro_name} disables these buttons: {affected}"
        return f"Unloading {self.macro_name} from Slot {self.slot_index + 1} disables: {affected}"

    @property
    def confirm_label(self) -> str:
        return {"replace": "Replace", "swap": "Swap", "delete": "Delete"}.get(self.operation, "Unload")


@dataclass(frozen=True, slots=True)
class LocalMacroPresentation:
    stable_id: str
    name: str
    source: str
    macro: MacroFile
    actions: tuple[MacroActionPresentation, ...]
    warnings: tuple[str, ...]
    loaded_slot: int | None
    dirty: bool
    editable: bool = True
    # Changed since the macro was selected (what Revert Macro undoes). The
    # library is saved automatically, so ``dirty`` (unsaved) is transient.
    edited: bool = False
    # Plain-language reasons the macro cannot be saved yet (press w/o release).
    unfinished: tuple[str, ...] = ()

    @property
    def incomplete(self) -> bool:
        return bool(self.warnings)

    # The page reads the current_* names; the library entry already holds edits.
    @property
    def current_actions(self) -> tuple[MacroActionPresentation, ...]:
        return self.actions

    @property
    def current_records(self) -> tuple[ActionRecord, ...]:
        return tuple(item.record for item in self.actions)

    @property
    def current_action_ids(self) -> tuple[str, ...]:
        return tuple(item.stable_id for item in self.actions)

    @property
    def current_action_count(self) -> int:
        return len(self.actions)

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def current_delay_total_ms(self) -> int:
        return sum(item.record.delay_ms for item in self.actions if item.record.action_type == ActionType.DELAY)

    encoded_delay_total_ms = current_delay_total_ms

    @property
    def current_loop_type(self) -> int:
        return self.macro.loop_type

    @property
    def current_loop_time(self) -> int:
        return self.macro.loop_time

    @property
    def current_loop_label(self) -> str:
        return LOOP_LABELS.get(self.current_loop_type, f"Unknown loop mode ({self.current_loop_type})")

    @property
    def current_loop_count(self) -> int | None:
        return self.current_loop_time if self.current_loop_type == 0 else None


@dataclass(frozen=True, slots=True)
class DeviceSlotPresentation:
    slot_index: int
    loaded_macro_id: str | None
    loaded_macro_name: str | None
    usage: tuple[MacroUsage, ...]
    mapping_note: str
    # The mouse holds a macro here that matches nothing in the library (read
    # from the mouse; shown, not added to the library).
    on_mouse_only: bool = False

    @property
    def stable_id(self) -> str:
        return f"device-macro-slot:{self.slot_index}"

    @property
    def label(self) -> str:
        return f"Slot {self.slot_index + 1}"

    @property
    def is_empty(self) -> bool:
        return self.loaded_macro_id is None and not self.on_mouse_only


# Validation codes for a press without its release: expected while a macro is
# being built or recorded, but the library will not save the macro until fixed.
UNFINISHED_CODES = frozenset({"missing-key-release", "missing-mouse-release"})


def friendly_issue(issue) -> str:
    """Plain-language text for a macro validation issue."""
    message = issue.message
    if issue.code == "missing-key-release":
        found = re.search(r"0x([0-9A-Fa-f]+)", message)
        name = key_name(int(found.group(1), 16)) if found else "A key"
        return f"Key {name} is pressed but never released"
    if issue.code == "missing-mouse-release":
        found = re.search(r"0x([0-9A-Fa-f]+)", message)
        button = MOUSE_MASK_NAMES.get(int(found.group(1), 16), "a") if found else "a"
        return f"The {button} mouse button is pressed but never released"
    if issue.code == "release-without-down":
        return "A release has no matching press before it"
    if issue.code == "duplicate-down":
        return "A key or button is pressed twice without a release in between"
    return message[:1].upper() + message[1:]


def _action_presentation(index: int, record: ActionRecord, stable_id: str) -> MacroActionPresentation:
    if record.action_type == ActionType.KEYBOARD:
        action = "Keyboard press" if record.state_down else "Keyboard release"
        return MacroActionPresentation(index, record, action, format_action_display_value(record), "—", stable_id=stable_id)
    if record.action_type == ActionType.MOUSE_BUTTON:
        action = "Mouse press" if record.state_down else "Mouse release"
        warning = None if record.usage in {1, 2, 4} else "Mouse action mask is unresolved"
        return MacroActionPresentation(index, record, action, format_action_display_value(record), "—", warning, stable_id)
    if record.action_type == ActionType.DELAY:
        return MacroActionPresentation(index, record, "Delay", "—", f"{record.delay_ms:,} ms", stable_id=stable_id)
    if record.action_type == ActionType.ZERO:
        return MacroActionPresentation(index, record, "Zero / padding", "—", "—", "Zero record inside Count", stable_id)
    return MacroActionPresentation(
        index, record, "Unknown action", "Raw record", "—",
        f"Preserved raw bytes: {record.raw.hex(' ').upper()}", stable_id,
    )


LIBRARY_ID_PREFIX = "local-macro:"


def library_stable_id(macro_id: str) -> str:
    return f"{LIBRARY_ID_PREFIX}{macro_id}"


def library_macro_id(stable_id: str) -> str:
    """Accept a presentation ID (``local-macro:<id>``) or a raw library ID."""
    return stable_id.removeprefix(LIBRARY_ID_PREFIX)


class MacroPresentationState:
    """Presentation adapter over the workspace's library and bank.

    Presentation IDs are ``local-macro:<library id>`` so drag payloads can be
    told apart from slot (``device-macro-slot:``) and action payloads.
    """

    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self._listeners: list[Callable[["MacroPresentationState"], None]] = []
        self._action_ids: dict[str, list[str]] = {}
        self._action_counter = 0
        self._clipboard: tuple[ActionRecord, ...] = ()
        self.selected_action_index: int | None = None
        self.pending_impact: MacroImpact | None = None
        entries = self.library.entries
        self.selected_macro_id: str | None = None
        self._snapshot: tuple[str, str, bytes] | None = None
        self._set_selected(library_stable_id(entries[0].macro_id) if entries else None)
        self.last_error: str | None = None
        workspace.subscribe(self._workspace_changed)

    # ------------------------------------------------------------ plumbing
    @property
    def library(self):
        return self.workspace.library

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener(self)

    def _workspace_changed(self, reason: str) -> None:
        entries = self.library.entries
        if self.selected_macro_id is not None and not any(
                entry.macro_id == library_macro_id(self.selected_macro_id) for entry in entries):
            self._set_selected(library_stable_id(entries[0].macro_id) if entries else None)
            self.selected_action_index = None
        if self.selected_macro_id is None and entries:
            self._set_selected(library_stable_id(entries[0].macro_id))
        if reason != "macro-edit":
            self._notify()

    def _set_selected(self, stable_id: str | None) -> None:
        """Select a macro and remember its state for Revert Macro."""
        self.selected_macro_id = stable_id
        self._snapshot = None
        if stable_id is None:
            return
        try:
            entry = self.library.entry(library_macro_id(stable_id))
        except MacroLibraryError:
            return
        self._snapshot = (entry.macro_id, entry.name, entry.macro.to_bytes())

    def _edited_since_selected(self, entry: LibraryMacro) -> bool:
        snapshot = self._snapshot
        if snapshot is None or snapshot[0] != entry.macro_id:
            return False
        return (entry.name, entry.macro.to_bytes()) != (snapshot[1], snapshot[2])

    def _macros_edited(self) -> None:
        self.workspace.notify("macro-edit")
        self._notify()

    def _new_action_id(self) -> str:
        self._action_counter += 1
        return f"local-action:{self._action_counter}"

    def _ids_for(self, entry: LibraryMacro, count: int) -> list[str]:
        ids = self._action_ids.get(entry.macro_id)
        if ids is None or len(ids) != count:
            ids = [self._new_action_id() for _ in range(count)]
            self._action_ids[entry.macro_id] = ids
        return ids

    # ------------------------------------------------------------ views
    def _present(self, entry: LibraryMacro) -> LocalMacroPresentation:
        macro = entry.macro
        editable = macro.has_action_blob
        warnings: list[str] = []
        unfinished: list[str] = []
        actions: tuple[MacroActionPresentation, ...] = ()
        if editable:
            try:
                records = macro.records
            except ValueError as exc:
                records = []
                editable = False
                warnings.append(str(exc))
            ids = self._ids_for(entry, len(records))
            actions = tuple(_action_presentation(index, record, ids[index]) for index, record in enumerate(records))
            for issue in validate_macro(macro):
                if issue.severity == "error" or issue.code in {"unknown-record", "data-after-count"}:
                    warnings.append(friendly_issue(issue))
                if issue.code in UNFINISHED_CODES:
                    unfinished.append(friendly_issue(issue))
            warnings.extend(item.warning for item in actions if item.warning)
        else:
            warnings.append("This macro file has no action data (MacroSetting_0) and cannot be edited.")
        return LocalMacroPresentation(
            stable_id=library_stable_id(entry.macro_id), name=entry.name, source=entry.source or "local library",
            macro=macro, actions=actions, warnings=tuple(warnings),
            loaded_slot=self.library.bank.slot_of(entry.macro_id), dirty=entry.dirty, editable=editable,
            edited=self._edited_since_selected(entry),
            unfinished=tuple(unfinished),
        )

    @property
    def local_macros(self) -> tuple[LocalMacroPresentation, ...]:
        return tuple(self._present(entry) for entry in self.library.entries)

    @property
    def selected_macro(self) -> LocalMacroPresentation | None:
        if self.selected_macro_id is None:
            return None
        try:
            return self._present(self.library.entry(library_macro_id(self.selected_macro_id)))
        except MacroLibraryError:
            return None

    def _usage(self, slot: int) -> tuple[MacroUsage, ...]:
        return tuple(
            MacroUsage(item.profile, item.button, item.description)
            for item in self.workspace.references_to_slot(slot)
        )

    @property
    def device_slots(self) -> tuple[DeviceSlotPresentation, ...]:
        slots = []
        for index, macro_id in enumerate(self.library.bank.slots):
            entry = self.library.entry(macro_id) if macro_id else None
            usage = self._usage(index)
            on_mouse = entry is None and index in self.library.mouse_only
            note = "Assigned to buttons" if usage else (
                "Not used by any button" if entry or on_mouse else "Empty")
            stable = library_stable_id(macro_id) if macro_id else None
            slots.append(DeviceSlotPresentation(index, stable, entry.name if entry else None, usage, note,
                                                on_mouse_only=on_mouse))
        return tuple(slots)

    @property
    def populated_slot_count(self) -> int:
        return sum(1 for item in self.library.bank.slots if item)

    @property
    def dirty(self) -> bool:
        return self.library.dirty

    # ------------------------------------------------------------ selection
    def select_macro(self, stable_id: str) -> None:
        self.library.entry(library_macro_id(stable_id))
        self._set_selected(library_stable_id(library_macro_id(stable_id)))
        self.selected_action_index = None
        self._notify()

    def select_action(self, index: int) -> None:
        selected = self.selected_macro
        if selected is None or not 0 <= index < selected.current_action_count:
            raise ValueError("unknown macro action index")
        self.selected_action_index = index
        self._notify()

    # ------------------------------------------------------------ library
    def add_local_macro(self, name: str = "New Macro") -> str:
        entry = self.library.create(name)
        self._set_selected(library_stable_id(entry.macro_id))
        self.selected_action_index = None
        self._macros_edited()
        return self.selected_macro_id

    def rename_selected_macro(self, name: str) -> None:
        if self.selected_macro_id is None:
            raise ValueError("no macro is selected")
        self.workspace.rename_macro(library_macro_id(self.selected_macro_id), name)

    def duplicate_selected_macro(self) -> str:
        if self.selected_macro_id is None:
            raise ValueError("no macro is selected")
        entry = self.library.duplicate(library_macro_id(self.selected_macro_id))
        self._set_selected(library_stable_id(entry.macro_id))
        self.selected_action_index = None
        self._macros_edited()
        return self.selected_macro_id

    def delete_selected_macro(self) -> MacroImpact | None:
        if self.selected_macro_id is None:
            raise ValueError("no macro is selected")
        entry = self.library.entry(library_macro_id(self.selected_macro_id))
        slot = self.library.bank.slot_of(entry.macro_id)
        usage = self._usage(slot) if slot is not None else ()
        if usage:
            self.pending_impact = MacroImpact("delete", slot, entry.name, None, usage,
                                              delete_macro_id=entry.macro_id)
            self._notify()
            return self.pending_impact
        self.workspace.delete_macro(entry.macro_id)
        return None

    def revert_selected_macro(self) -> None:
        if self.selected_macro_id is None:
            return
        entry = self.library.entry(library_macro_id(self.selected_macro_id))
        snapshot = self._snapshot
        if snapshot is None or snapshot[0] != entry.macro_id or not self._edited_since_selected(entry):
            return
        _macro_id, name, payload = snapshot
        entry.macro = MacroFile.from_bytes(payload)
        if entry.name != name:
            # Rename back through the workspace so button references follow.
            self.workspace.rename_macro(entry.macro_id, name)
        self._action_ids.pop(entry.macro_id, None)
        self.selected_action_index = None
        self._macros_edited()

    # ------------------------------------------------------------ action edits
    def _require_entry(self) -> LibraryMacro:
        if self.selected_macro_id is None:
            raise ValueError("no macro is selected")
        entry = self.library.entry(library_macro_id(self.selected_macro_id))
        if not entry.macro.has_action_blob:
            raise ValueError("this macro has no editable action data")
        return entry

    def _write(self, entry: LibraryMacro, records: list[ActionRecord], ids: list[str]) -> None:
        if len(records) > ACTION_RECORD_CAPACITY:
            raise ValueError("macro action capacity is 63 records")
        entry.macro.set_actions(records)
        self._action_ids[entry.macro_id] = list(ids)
        self._macros_edited()

    def _current(self) -> tuple[LibraryMacro, list[ActionRecord], list[str]]:
        entry = self._require_entry()
        records = list(entry.macro.records)
        ids = list(self._ids_for(entry, len(records)))
        return entry, records, ids

    def _require_action(self) -> int:
        if self.selected_action_index is None:
            raise ValueError("no macro action is selected")
        return self.selected_action_index

    def add_action(self, record: ActionRecord, position: int | None = None) -> None:
        if record.action_type not in {ActionType.KEYBOARD, ActionType.MOUSE_BUTTON, ActionType.DELAY}:
            raise ValueError("only confirmed action families can be inserted")
        entry, records, ids = self._current()
        if len(records) >= ACTION_RECORD_CAPACITY:
            raise ValueError("macro action capacity is 63 records")
        index = len(records) if position is None else max(0, min(position, len(records)))
        records.insert(index, record)
        ids.insert(index, self._new_action_id())
        self.selected_action_index = index
        self._write(entry, records, ids)

    def add_actions(self, new_records: list[ActionRecord]) -> None:
        entry, records, ids = self._current()
        if len(records) + len(new_records) > ACTION_RECORD_CAPACITY:
            raise ValueError("macro action capacity is 63 records")
        records.extend(new_records)
        ids.extend(self._new_action_id() for _ in new_records)
        self.selected_action_index = len(records) - 1 if records else None
        self._write(entry, records, ids)

    def delete_selected_action(self) -> None:
        index = self._require_action()
        entry, records, ids = self._current()
        records.pop(index)
        ids.pop(index)
        self.selected_action_index = min(index, len(records) - 1) if records else None
        self._write(entry, records, ids)

    def move_selected_action(self, direction: int) -> None:
        index = self._require_action()
        entry, records, ids = self._current()
        target = index + direction
        if not 0 <= target < len(records):
            return
        records[index], records[target] = records[target], records[index]
        ids[index], ids[target] = ids[target], ids[index]
        self.selected_action_index = target
        self._write(entry, records, ids)

    def reorder_action(self, source_index: int, target_index: int) -> None:
        entry, records, ids = self._current()
        if not 0 <= source_index < len(records) or not 0 <= target_index < len(records):
            raise ValueError("unknown macro action index")
        if source_index == target_index:
            self.selected_action_index = source_index
            return
        record = records.pop(source_index)
        action_id = ids.pop(source_index)
        records.insert(min(target_index, len(records)), record)
        ids.insert(min(target_index, len(ids)), action_id)
        self.selected_action_index = min(target_index, len(records) - 1)
        self._write(entry, records, ids)

    def copy_selected_action(self) -> None:
        entry, records, _ids = self._current()
        self._clipboard = (records[self._require_action()],)

    def cut_selected_action(self) -> None:
        self.copy_selected_action()
        self.delete_selected_action()

    def paste_action(self) -> None:
        if not self._clipboard:
            raise ValueError("clipboard is empty")
        entry, records, ids = self._current()
        if len(records) + len(self._clipboard) > ACTION_RECORD_CAPACITY:
            raise ValueError("macro action capacity is 63 records")
        index = self.selected_action_index
        insert_at = len(records) if index is None else index + 1
        records[insert_at:insert_at] = self._clipboard
        ids[insert_at:insert_at] = [self._new_action_id() for _ in self._clipboard]
        self.selected_action_index = insert_at
        self._write(entry, records, ids)

    def _action_index(self, action_id: str) -> int:
        entry, records, ids = self._current()
        try:
            return ids.index(action_id)
        except ValueError as error:
            raise ValueError("unknown macro action identity") from error

    def edit_selected_delay(self, milliseconds: int) -> None:
        index = self._require_action()
        entry, records, ids = self._current()
        self.edit_action_delay(ids[index], milliseconds)

    def edit_action_delay(self, action_id: str, milliseconds: int) -> None:
        if (isinstance(milliseconds, bool) or not isinstance(milliseconds, int)
                or milliseconds % ACTION_DELAY_QUANTUM_MS or not 0 <= milliseconds <= ACTION_DELAY_MAX_MS):
            raise ValueError("action delay must use the confirmed 10 ms granularity")
        index = self._action_index(action_id)
        entry, records, ids = self._current()
        if records[index].action_type != ActionType.DELAY:
            raise ValueError("only explicit delay records have editable delay values")
        records[index] = ActionRecord.action_delay_ms(milliseconds)
        self.selected_action_index = index
        self._write(entry, records, ids)

    def edit_selected_usage(self, usage: int) -> None:
        index = self._require_action()
        entry, records, ids = self._current()
        self.edit_action_usage(ids[index], usage)

    def edit_action_usage(self, action_id: str, usage: int) -> None:
        if isinstance(usage, bool) or not isinstance(usage, int) or not 0 <= usage <= 0xFF:
            raise ValueError("action usage must be an 8-bit value")
        index = self._action_index(action_id)
        entry, records, ids = self._current()
        record = records[index]
        if record.action_type == ActionType.KEYBOARD:
            records[index] = ActionRecord.keyboard(usage, record.state_down)
        elif record.action_type == ActionType.MOUSE_BUTTON:
            records[index] = ActionRecord.mouse_button(usage, record.state_down)
        else:
            raise ValueError("only keyboard and mouse actions have editable usages")
        self.selected_action_index = index
        self._write(entry, records, ids)

    def unfinished_macro_problems(self) -> list[str]:
        """Plain-language problems that stop any library macro being saved."""
        problems = []
        for entry in self.library.entries:
            if not entry.macro.has_action_blob:
                continue
            try:
                issues = validate_macro(entry.macro)
            except ValueError:
                continue
            for issue in issues:
                if issue.severity == "error":
                    problems.append(f"{entry.name}: {friendly_issue(issue)}")
        return problems

    def edit_delay_settings(self, delay_type: int, default_delay_ms: int | None = None) -> None:
        """Set the macro's delay mode (record / default / none) and default ms."""
        entry = self._require_entry()
        default = entry.macro.default_inserted_delay_ms if default_delay_ms is None else default_delay_ms
        if (entry.macro.delay_type, entry.macro.default_inserted_delay_ms) == (delay_type, default):
            return
        try:
            set_delay_settings(entry.macro, delay_type, default)
        except (KeyError, TypeError, ValueError, MacroLibraryError) as exc:
            raise ValueError(str(exc)) from exc
        self._macros_edited()

    def delay_between_actions_ms(self) -> int:
        """Delay placed between non-recorded actions for the selected macro.

        Default-delay mode uses the macro's default (Windows rules); otherwise
        the 10 ms minimum keeps consecutive actions distinct.
        """
        macro = self._require_entry().macro
        if macro.delay_type == DELAY_TYPE_DEFAULT:
            return effective_default_delay_ms(macro.default_inserted_delay_ms)
        return MINIMUM_ACTION_DELAY_MS

    def insert_with_default_delay(self, record: ActionRecord) -> None:
        """Append an action with a delay before it (see delay_between_actions_ms).

        The delay is added only between actions: not before the first one and
        not directly after an existing delay.
        """
        entry, records, _ids = self._current()
        new = [record]
        if (records and records[-1].action_type is not ActionType.DELAY
                and record.action_type is not ActionType.DELAY):
            new.insert(0, ActionRecord.action_delay_ms(self.delay_between_actions_ms()))
        if len(new) == 1:
            self.add_action(record)
        else:
            self.add_actions(new)

    def edit_loop(self, loop_type: int, loop_time: int | None = None) -> None:
        entry = self._require_entry()
        if loop_type not in LOOP_LABELS:
            raise ValueError("unknown loop mode")
        time = entry.macro.loop_time if loop_time is None else loop_time
        if loop_type == 0 and not 1 <= time <= LOOP_COUNT_MAX:
            raise ValueError(f"fixed repeat count must be 1..{LOOP_COUNT_MAX}")
        if time < 1:
            time = 1
        if entry.macro.loop_type == loop_type and entry.macro.loop_time == time:
            return
        try:
            set_loop(entry.macro, loop_type, time)
        except (KeyError, MacroLibraryError) as exc:
            raise ValueError(str(exc)) from exc
        self.workspace.mark_macro_loop_changed(entry.macro_id)
        self._macros_edited()

    # ------------------------------------------------------------ bank
    def drop_library_on_slot(self, stable_id: str, slot_index: int) -> MacroImpact | None:
        macro_id = library_macro_id(stable_id)
        entry = self.library.entry(macro_id)
        current = self.library.bank.slots[slot_index]
        if current == macro_id:
            return None
        usage = self._usage(slot_index) if current else ()
        if current is not None and usage:
            self.pending_impact = MacroImpact(
                "replace", slot_index, self.library.entry(current).name, entry.name, usage,
                replacement_id=macro_id,
            )
            self._notify()
            return self.pending_impact
        self.workspace.load_macro_into_slot(macro_id, slot_index)
        self.pending_impact = None
        return None

    def unload_slot(self, slot_index: int) -> MacroImpact | None:
        current = self.library.bank.slots[slot_index]
        if current is None:
            return None
        usage = self._usage(slot_index)
        if usage:
            self.pending_impact = MacroImpact("unload", slot_index, self.library.entry(current).name, None, usage)
            self._notify()
            return self.pending_impact
        self.workspace.unload_slot(slot_index)
        return None

    def swap_slots(self, first: int, second: int) -> MacroImpact | None:
        """Swap two slots without asking: buttons follow their macros (and are sent with
        the slot uploads), so no button changes what it does (user decision, 2026-11-19)."""
        if first == second:
            return None
        self.workspace.swap_slots(first, second)
        return None

    def confirm_pending_impact(self) -> None:
        pending = self.pending_impact
        if pending is None:
            return
        self.pending_impact = None
        if pending.operation == "replace":
            self.workspace.load_macro_into_slot(library_macro_id(pending.replacement_id or ""), pending.slot_index)
        elif pending.operation == "swap":
            self.workspace.swap_slots(pending.slot_index, pending.other_slot)
        elif pending.operation == "delete":
            self.workspace.delete_macro(pending.delete_macro_id or "")
        else:
            self.workspace.unload_slot(pending.slot_index)
        self._notify()

    def cancel_pending_impact(self) -> None:
        self.pending_impact = None
        self._notify()
