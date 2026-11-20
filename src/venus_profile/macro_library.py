"""Unlimited local macro library and the 15-slot active device bank.

Windows keeps macros as ``MacroDB/<name>.MSMACRO`` files and lists the active
device slots in ``MacroDB/MacroSet.MSDB`` (``[MACRO_LIST]`` 0..14).  Its editor
refuses to create more than fifteen macros ("TOO MANY MACROS TO BE CREATE!").
Profiles reference a macro with ``ButtonFunc=100``, ``KeyNumber=<slot>`` and
``MacroName=<name>``; the macro body itself is never stored in a PFD.

The Linux design deliberately separates:

* the **library**: any number of MSMACRO files, and
* the **bank**: exactly fifteen device-slot references into the library.

Storage is a plain folder (``~/Documents/VenusMouse/Macros`` by default, see
:func:`~venus_profile.app_store.default_macro_library_dir`) in the Windows
formats: one ``<name>.MSMACRO`` per macro and ``MacroSet.MSDB`` for the bank,
so the folder is the library and can be copied to another computer. The
library only replaces files it wrote itself (its own macros and
``MacroSet.MSDB``), never clobbers an unknown file, writes atomically, and
sends deleted macros to the desktop trash (recoverable until emptied).

Only bank slots 0..14 are eligible for an eventual device upload.  Device
regions 15..19 are never represented.  This module has no device access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
import uuid

from .app_store import AppStore, StoreError
from .macro import MacroFile
from .macroset import MACRO_SLOT_COUNT, MacroSet
from .validation import validate_macro, validate_macroset

LIBRARY_VERSION = 1
MACROSET_FILE_NAME = "MacroSet.MSDB"
MACRO_SUFFIX = ".MSMACRO"
NAME_MAX_LENGTH = 48
_FORBIDDEN_NAME_CHARACTERS = set('\\/:*?"<>|')
_MACROSET_TEMPLATE = (
    "[MACRO_LIST]\r\n"
    + "".join(f"{slot}=\r\n" for slot in range(MACRO_SLOT_COUNT))
    + "[Setting]\r\nDevice=1\r\nFirstOpen=1\r\n"
)


class MacroLibraryError(ValueError):
    """A library or bank operation was rejected."""


def validate_macro_name(name: str) -> str:
    """Return a cleaned name that is safe as a Windows MSMACRO file stem."""
    if not isinstance(name, str):
        raise MacroLibraryError("macro name must be text")
    clean = name.strip()
    if not clean:
        raise MacroLibraryError("macro name cannot be empty")
    if len(clean) > NAME_MAX_LENGTH:
        raise MacroLibraryError(f"macro name is limited to {NAME_MAX_LENGTH} characters")
    if not clean.isascii() or not clean.isprintable():
        raise MacroLibraryError("macro name must use printable ASCII characters")
    if any(character in _FORBIDDEN_NAME_CHARACTERS for character in clean):
        raise MacroLibraryError('macro name cannot contain \\ / : * ? " < > |')
    if clean.endswith(".") or clean in {".", ".."}:
        raise MacroLibraryError("macro name cannot end with a period")
    return clean


def macro_issues_blocking(macro: MacroFile) -> list[str]:
    return [issue.message for issue in validate_macro(macro) if issue.severity == "error"]


@dataclass(slots=True)
class LibraryMacro:
    """One library entry: a working MSMACRO plus its last-saved bytes."""

    macro_id: str
    name: str
    macro: MacroFile
    saved_name: str
    saved_bytes: bytes
    source: str = ""

    @property
    def dirty(self) -> bool:
        return self.name != self.saved_name or self.macro.to_bytes() != self.saved_bytes

    @property
    def is_new(self) -> bool:
        return not self.saved_bytes

    def revert(self) -> None:
        if self.is_new:
            raise MacroLibraryError("a macro that was never saved cannot be reverted")
        self.macro = MacroFile.from_bytes(self.saved_bytes)
        self.name = self.saved_name


@dataclass(slots=True)
class DeviceBank:
    """Fifteen slot references (library IDs or None)."""

    slots: list[str | None] = field(default_factory=lambda: [None] * MACRO_SLOT_COUNT)
    saved_slots: tuple[str | None, ...] = field(
        default_factory=lambda: (None,) * MACRO_SLOT_COUNT
    )

    def __post_init__(self) -> None:
        if len(self.slots) != MACRO_SLOT_COUNT:
            raise MacroLibraryError("the device bank must contain exactly 15 slots")

    @property
    def dirty(self) -> bool:
        return tuple(self.slots) != tuple(self.saved_slots)

    def slot_of(self, macro_id: str) -> int | None:
        try:
            return self.slots.index(macro_id)
        except ValueError:
            return None

    @staticmethod
    def check_slot(slot: int) -> int:
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < MACRO_SLOT_COUNT:
            raise MacroLibraryError("device macro slot must be 1..15")
        return slot


def _write_file_atomic(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` via a same-folder temp file + rename."""
    temp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
    except OSError as exc:
        raise MacroLibraryError(f"could not write {path.name}: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def trash_file(path: Path) -> None:
    """Move a file to the desktop trash (Gio), so a delete is recoverable."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as exc:
        raise MacroLibraryError(f"cannot move {path.name} to the trash: {exc}") from exc
    try:
        Gio.File.new_for_path(str(path)).trash(None)
    except GLib.Error as exc:
        raise MacroLibraryError(f"could not move {path.name} to the trash: {exc.message}") from exc


class MacroLibrary:
    """Library plus bank, stored as Windows-format files in a folder."""

    # Replaceable for tests; must move ``path`` to a recoverable trash.
    trash = staticmethod(trash_file)

    def __init__(self, store: AppStore, folder: str | Path | None = None) -> None:
        self.store = store
        self.folder = Path(folder) if folder is not None else store.macro_library_dir
        self.entries: list[LibraryMacro] = []
        self.bank = DeviceBank()
        self._deleted: list[LibraryMacro] = []
        self.load_notes: list[str] = []
        # Device-slot bodies read from the mouse that match no library macro:
        # kept in memory only (never written to the library folder) so the
        # slot shows what the mouse holds. Loading, unloading or swapping the
        # slot replaces/clears/moves them like any slot content.
        self.mouse_only: dict[int, bytes] = {}

    # ------------------------------------------------------------ loading
    @classmethod
    def open(cls, store: AppStore, folder: str | Path | None = None) -> "MacroLibrary":
        library = cls(store, folder)
        library._migrate_from_app_store()
        library._load()
        return library

    def _macro_path(self, name: str) -> Path:
        return self.folder / f"{name}{MACRO_SUFFIX}"

    def _macro_files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted((path for path in self.folder.iterdir()
                       if path.is_file() and path.suffix.lower() == MACRO_SUFFIX.lower()
                       and not path.name.startswith(".")),
                      key=lambda path: path.stem.casefold())

    def _load(self) -> None:
        self.load_notes = []
        for path in self._macro_files():
            try:
                name = validate_macro_name(path.stem)
            except MacroLibraryError as exc:
                self.load_notes.append(f"{path.name} was skipped: {exc}")
                continue
            if self.find_by_name(name) is not None:
                self.load_notes.append(f"{path.name} was skipped: another macro has the same name")
                continue
            try:
                data = path.read_bytes()
                macro = MacroFile.from_bytes(data)
            except (OSError, UnicodeError, ValueError) as exc:
                self.load_notes.append(f"{path.name} could not be read: {exc}")
                continue
            # saved_name doubles as the file the entry was loaded from.
            self.entries.append(LibraryMacro(uuid.uuid4().hex, name, macro, name, data, "macro library"))
        cleaned: list[str | None] = [None] * MACRO_SLOT_COUNT
        macroset_path = self.folder / MACROSET_FILE_NAME
        if macroset_path.exists():
            try:
                names = MacroSet.from_path(macroset_path).names
            except (OSError, UnicodeError, ValueError) as exc:
                self.load_notes.append(f"{MACROSET_FILE_NAME} could not be read: {exc}")
                names = []
            for slot, name in enumerate(names[:MACRO_SLOT_COUNT]):
                if not name:
                    continue
                entry = self.find_by_name(name)
                if entry is None:
                    self.load_notes.append(f"Device slot {slot + 1} lists {name!r}, which is not in the library")
                elif entry.macro_id not in cleaned:
                    cleaned[slot] = entry.macro_id
        self.bank = DeviceBank(list(cleaned), tuple(cleaned))

    def _migrate_from_app_store(self) -> None:
        """One-time move of a library kept in the application store.

        Earlier versions stored ``<id>.MSMACRO`` files plus JSON indexes in
        the app store. If the folder holds no library yet, those macros are
        copied in under their names and the old index is renamed (kept).
        """
        index_path = self.store.library_index_path
        if not index_path.exists() or self._macro_files() or (self.folder / MACROSET_FILE_NAME).exists():
            return
        try:
            index = self.store.read_json(index_path, {}) or {}
            bank = self.store.read_json(self.store.bank_path, {}) or {}
        except StoreError as exc:
            self.load_notes.append(f"Old macro library could not be read: {exc}")
            return
        names_by_id: dict[str, str] = {}
        for record in index.get("macros", []) if isinstance(index, dict) else []:
            try:
                macro_id, name = str(record["id"]), validate_macro_name(str(record["name"]))
                data = (self.store.library_dir / f"{macro_id}{MACRO_SUFFIX}").read_bytes()
            except (KeyError, OSError, TypeError, MacroLibraryError):
                continue
            target = self._macro_path(name)
            if target.exists():
                continue
            _write_file_atomic(target, data)
            names_by_id[macro_id] = name
        slots = bank.get("slots") if isinstance(bank, dict) else None
        if isinstance(slots, list) and len(slots) == MACRO_SLOT_COUNT:
            macro_set = MacroSet.from_bytes(_MACROSET_TEMPLATE.encode("ascii"))
            for slot, macro_id in enumerate(slots):
                if isinstance(macro_id, str) and macro_id in names_by_id:
                    macro_set.set_name(slot, names_by_id[macro_id])
            _write_file_atomic(self.folder / MACROSET_FILE_NAME, macro_set.to_bytes())
        try:
            self.store.move_to(index_path, index_path.with_name(index_path.name + ".migrated"))
        except StoreError:
            pass

    # ------------------------------------------------------------ queries
    @property
    def dirty(self) -> bool:
        return (self.bank.dirty or bool(self._deleted)
                or any(entry.dirty for entry in self.entries))

    def entry(self, macro_id: str) -> LibraryMacro:
        for entry in self.entries:
            if entry.macro_id == macro_id:
                return entry
        raise MacroLibraryError("unknown library macro")

    def find_by_name(self, name: str) -> LibraryMacro | None:
        folded = name.casefold()
        return next((entry for entry in self.entries if entry.name.casefold() == folded), None)

    def slot_macro(self, slot: int) -> LibraryMacro | None:
        macro_id = self.bank.slots[DeviceBank.check_slot(slot)]
        return self.entry(macro_id) if macro_id else None

    def unique_name(self, base: str) -> str:
        stem = validate_macro_name(base)[: NAME_MAX_LENGTH - 4]
        if self.find_by_name(stem) is None:
            return stem
        counter = 2
        while self.find_by_name(f"{stem} {counter}") is not None:
            counter += 1
        return f"{stem} {counter}"

    # ------------------------------------------------------------ editing
    def create(self, name: str = "New Macro") -> LibraryMacro:
        clean = self.unique_name(name)
        # New macros record real timings by default (user decision 2026-11-19).
        macro = MacroFile.create([], macro_file_path=clean, delay_type=DELAY_TYPE_RECORDED)
        entry = LibraryMacro(uuid.uuid4().hex, clean, macro, "", b"", "created in Linux")
        self.entries.append(entry)
        return entry

    def duplicate(self, macro_id: str) -> LibraryMacro:
        source = self.entry(macro_id)
        name = self.unique_name(f"{source.name} Copy")
        macro = MacroFile.from_bytes(source.macro.to_bytes())
        _set_macro_file_path(macro, name)
        entry = LibraryMacro(uuid.uuid4().hex, name, macro, "", b"", f"copy of {source.name}")
        self.entries.append(entry)
        return entry

    def rename(self, macro_id: str, name: str) -> LibraryMacro:
        entry = self.entry(macro_id)
        clean = validate_macro_name(name)
        other = self.find_by_name(clean)
        if other is not None and other.macro_id != macro_id:
            raise MacroLibraryError(f"a macro named {clean!r} already exists")
        entry.name = clean
        _set_macro_file_path(entry.macro, clean)
        return entry

    def delete(self, macro_id: str) -> LibraryMacro:
        entry = self.entry(macro_id)
        self.entries.remove(entry)
        slot = self.bank.slot_of(macro_id)
        if slot is not None:
            self.bank.slots[slot] = None
        if not entry.is_new:
            self._deleted.append(entry)
        return entry

    def restore_deleted(self, macro_id: str) -> LibraryMacro:
        """Undo a not-yet-saved deletion."""
        for entry in self._deleted:
            if entry.macro_id == macro_id:
                self._deleted.remove(entry)
                self.entries.append(entry)
                return entry
        raise MacroLibraryError("no pending deletion for that macro")

    def import_file(self, path: str | Path) -> LibraryMacro:
        """Import one Windows MSMACRO (or macro-shaped MSDB) file."""
        source = Path(path)
        try:
            data = source.read_bytes()
            macro = MacroFile.from_bytes(data)
        except (OSError, UnicodeError, ValueError) as exc:
            raise MacroLibraryError(f"could not read macro file {source.name}: {exc}") from exc
        if not macro.has_action_blob:
            raise MacroLibraryError(f"{source.name} has no macro action data (MacroSetting_0)")
        errors = macro_issues_blocking(macro)
        if errors:
            raise MacroLibraryError(f"{source.name} is not a valid macro: " + "; ".join(errors))
        try:
            base = validate_macro_name(source.stem)
        except MacroLibraryError:
            base = "Imported Macro"
        name = self.unique_name(base)
        entry = LibraryMacro(uuid.uuid4().hex, name, macro, "", b"", f"imported from {source.name}")
        if name != source.stem:
            _set_macro_file_path(entry.macro, name)
        self.entries.append(entry)
        return entry

    def import_macrodb(self, folder: str | Path, *, populate_bank: bool) -> tuple[list[LibraryMacro], list[str]]:
        """Import every macro in a Windows MacroDB folder.

        When ``populate_bank`` is true, slots listed in the folder's
        ``MacroSet.MSDB`` are loaded into the matching empty bank slots, so
        imported profiles that reference those slots keep working.
        Returns the imported entries and human-readable notes.
        """
        root = Path(folder)
        if not root.is_dir():
            raise MacroLibraryError(f"{root} is not a folder")
        notes: list[str] = []
        imported: list[LibraryMacro] = []
        by_stem: dict[str, LibraryMacro] = {}
        for path in sorted(root.glob("*.MSMACRO")) + sorted(root.glob("*.msmacro")):
            if path.stem in by_stem:
                continue
            try:
                entry = self.import_file(path)
            except MacroLibraryError as exc:
                notes.append(str(exc))
                continue
            imported.append(entry)
            by_stem[path.stem] = entry
        macroset_path = next((path for path in root.iterdir()
                              if path.name.lower() == "macroset.msdb"), None)
        if populate_bank and macroset_path is not None:
            try:
                macro_set = MacroSet.from_path(macroset_path)
            except (OSError, UnicodeError, ValueError) as exc:
                notes.append(f"MacroSet.MSDB could not be read: {exc}")
            else:
                for slot, name in enumerate(macro_set.names):
                    if not name:
                        continue
                    entry = by_stem.get(name)
                    if entry is None:
                        notes.append(f"MacroSet slot {slot + 1} lists {name!r}, which has no macro file")
                        continue
                    if self.bank.slots[slot] is not None:
                        notes.append(f"Slot {slot + 1} is already occupied; {entry.name!r} was not loaded")
                        continue
                    if self.bank.slot_of(entry.macro_id) is None:
                        self.bank.slots[slot] = entry.macro_id
        return imported, notes

    # ------------------------------------------------------------ bank
    def load_into_slot(self, macro_id: str, slot: int) -> str | None:
        """Place a macro in a slot; returns the replaced macro ID, if any.

        A macro occupies at most one slot (Windows MacroSet names are
        unique), so loading it elsewhere moves it.
        """
        DeviceBank.check_slot(slot)
        self.entry(macro_id)
        previous = self.bank.slots[slot]
        if previous == macro_id:
            return None
        current = self.bank.slot_of(macro_id)
        if current is not None:
            self.bank.slots[current] = None
        self.bank.slots[slot] = macro_id
        self.mouse_only.pop(slot, None)
        return previous

    def unload_slot(self, slot: int) -> str | None:
        DeviceBank.check_slot(slot)
        previous = self.bank.slots[slot]
        self.bank.slots[slot] = None
        self.mouse_only.pop(slot, None)
        return previous

    def swap_slots(self, first: int, second: int) -> None:
        DeviceBank.check_slot(first)
        DeviceBank.check_slot(second)
        slots = self.bank.slots
        slots[first], slots[second] = slots[second], slots[first]
        first_body, second_body = self.mouse_only.pop(first, None), self.mouse_only.pop(second, None)
        if first_body is not None:
            self.mouse_only[second] = first_body
        if second_body is not None:
            self.mouse_only[first] = second_body

    def macroset(self) -> MacroSet:
        """Build a Windows-compatible MacroSet for the current bank."""
        macro_set = MacroSet.from_bytes(_MACROSET_TEMPLATE.encode("ascii"))
        for slot, macro_id in enumerate(self.bank.slots):
            if macro_id:
                macro_set.set_name(slot, self.entry(macro_id).name)
        issues = [issue for issue in validate_macroset(macro_set) if issue.severity == "error"]
        if issues:
            raise MacroLibraryError("; ".join(issue.message for issue in issues))
        return macro_set

    # ------------------------------------------------------------ saving
    def save(self) -> None:
        """Write changed macros, trash deleted ones, and write MacroSet.MSDB."""
        for entry in self.entries:
            errors = macro_issues_blocking(entry.macro)
            if errors:
                raise MacroLibraryError(f"{entry.name}: " + "; ".join(errors))
        # Deletions first, so a new macro may reuse a just-deleted name.
        for entry in list(self._deleted):
            source = self._macro_path(entry.saved_name)
            if source.exists():
                type(self).trash(source)
            self._deleted.remove(entry)
        own_files = {entry.saved_name.casefold() for entry in self.entries if not entry.is_new}
        for entry in self.entries:
            if not entry.dirty:
                continue
            target = self._macro_path(entry.name)
            renamed = entry.is_new or entry.name.casefold() != entry.saved_name.casefold()
            if renamed and target.exists() and entry.name.casefold() not in own_files:
                # A file the library did not write: never overwrite it.
                raise MacroLibraryError(
                    f"a file named {target.name} already exists in {self.folder}; rename the macro"
                )
            payload = entry.macro.to_bytes()
            _write_file_atomic(target, payload)
            if not entry.is_new and entry.saved_name and entry.name != entry.saved_name:
                old = self._macro_path(entry.saved_name)
                if old.exists() and old != target:
                    old.unlink()  # our own file, now saved under its new name
            entry.saved_bytes = payload
            entry.saved_name = entry.name
        _write_file_atomic(self.folder / MACROSET_FILE_NAME, self._bank_macroset().to_bytes())
        self.bank.saved_slots = tuple(self.bank.slots)

    def _bank_macroset(self) -> MacroSet:
        macro_set = MacroSet.from_bytes(_MACROSET_TEMPLATE.encode("ascii"))
        for slot, macro_id in enumerate(self.bank.slots):
            if macro_id:
                macro_set.set_name(slot, self.entry(macro_id).name)
        return macro_set

    def revert(self) -> None:
        """Discard every unsaved library and bank change."""
        self.entries = []
        self._deleted = []
        self.mouse_only = {}
        self._load()

    def export_windows_macrodb(self, folder: str | Path) -> Path:
        """Write the bank as a Windows-style MacroDB folder (new folder only)."""
        target = Path(folder)
        if target.exists():
            raise MacroLibraryError(f"{target} already exists; choose a new folder name")
        macro_set = self.macroset()
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            (staging / "MacroSet.MSDB").write_bytes(macro_set.to_bytes())
            for macro_id in self.bank.slots:
                if macro_id:
                    entry = self.entry(macro_id)
                    (staging / f"{entry.name}.MSMACRO").write_bytes(entry.macro.to_bytes())
            os.rename(staging, target)
        except OSError as exc:
            for child in staging.glob("*"):
                child.unlink(missing_ok=True)
            staging.rmdir()
            raise MacroLibraryError(f"could not export macros: {exc}") from exc
        return target


def _set_macro_file_path(macro: MacroFile, name: str) -> None:
    """Point MacroFilePath at ``name`` while keeping any directory prefix."""
    try:
        current = macro.macro_file_path
    except ValueError:
        return
    prefix, separator, _leaf = current.replace("/", "\\").rpartition("\\")
    leaf = f"{name}.MSMACRO" if current.upper().endswith(".MSMACRO") else name
    value = f"{prefix}{separator}{leaf}" if separator else leaf
    if value != current:
        macro.document.set("Setting", "MacroFilePath", value)


def set_loop(macro: MacroFile, loop_type: int, loop_time: int) -> None:
    """Set LoopType/LoopTime metadata (only fields already present)."""
    if loop_type not in (0, 1, 2, 3):
        raise MacroLibraryError("loop type must be 0..3")
    if isinstance(loop_time, bool) or not isinstance(loop_time, int) or not 1 <= loop_time <= 0xFFFF:
        raise MacroLibraryError("loop count must be 1..65535")
    for key, value in (("LoopType", str(loop_type)), ("LoopTime", str(loop_time))):
        if macro.document.get("Setting", key) != value:
            macro.document.set("Setting", key, value)


# DelayType values, confirmed from Windows-saved files
# (investigation/test_profile_matrix.txt, investigation/macro_analysis.txt).
DELAY_TYPE_RECORDED = 0   # RECORD DELAYS BETWEEN ACTION
DELAY_TYPE_DEFAULT = 1    # INSERT DEFAULT DELAY
DELAY_TYPE_NONE = 2       # neither option checked: minimum delay between actions
# Delay placed between actions when neither option is checked. Saved Windows
# files show "no delay" macros still carry 10 ms records; 10 ms keeps two
# actions distinct while being effectively instant.
MINIMUM_ACTION_DELAY_MS = 10


def set_delay_settings(macro: MacroFile, delay_type: int, default_delay_ms: int) -> None:
    """Set DelayType/DefDelayTime editor metadata (records are not changed)."""
    if delay_type not in (DELAY_TYPE_RECORDED, DELAY_TYPE_DEFAULT, DELAY_TYPE_NONE):
        raise MacroLibraryError("delay type must be 0..2")
    macro.set_default_inserted_delay_ms(default_delay_ms)
    if macro.document.get("Setting", "DelayType") != str(delay_type):
        macro.document.set("Setting", "DelayType", str(delay_type))


def effective_default_delay_ms(typed_ms: int) -> int:
    """The delay the Windows editor inserts for a typed default delay.

    ldcfg.exe ``sub_424a30``: values under 10 (including an empty field, read
    as 0) become 10, then the value is rounded down to a multiple of 10 ms
    (``investigation/REA_FINDINGS.md``, "Macro editor default delay").
    """
    value = max(10, int(typed_ms))
    return min(value - value % 10, 0xFFFF - 0xFFFF % 10)


__all__ = [
    "DELAY_TYPE_DEFAULT", "DELAY_TYPE_NONE", "DELAY_TYPE_RECORDED", "MINIMUM_ACTION_DELAY_MS",
    "DeviceBank", "LibraryMacro", "MacroLibrary", "MacroLibraryError",
    "StoreError", "effective_default_delay_ms", "set_delay_settings",
    "set_loop", "validate_macro_name",
]
