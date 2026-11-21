"""Offline workspace coordinating five profiles, the macro library and bank.

Windows behaviour mirrored here (``docs/WINDOWS_WORKFLOW.md``):

* five working profiles are loaded at start-up and written back by the
  application (``RSProfile1..5.pfd``); Linux keeps them in the application
  store, saves edits to them automatically (the GUI persists the working set
  shortly after each change) and restores them on the next launch;
* *Load File* copies a named PFD into the current slot; editing the slot
  never changes that file. *Save As* is the only way to write a named file;
* *Cancel* discards edits made since the slot's profile was loaded (or last
  saved as / restored), returning to that loaded profile;
* *Restore* resets the current profile and *Reset All* resets every profile
  to the shipped factory files;
* buttons reference macros by device slot (``KeyNumber``) and name
  (``MacroName``).

Nothing here communicates with a device; Apply remains a separate, disabled
boundary.  All writes go to application-owned files or no-clobber exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import button_functions as bf
from .app_store import AppStore, StoreError, factory_profile_bytes
from .macro_library import MacroLibrary, MacroLibraryError
from .pfd import PfdProfile
from .profile_session import (
    ProfileDocumentSession,
    ProfileLoadError,
    ProfileSaveError,
    ProfileSetSession,
)

SLOTS = (1, 2, 3, 4, 5)
# Origin marker for a slot whose content was replaced by a read of the mouse.
FROM_MOUSE_ORIGIN = "from mouse"


@dataclass(frozen=True, slots=True)
class MacroReference:
    """One button in one profile that references a device macro slot."""

    profile: int
    button: int
    slot: int
    macro_name: str

    @property
    def description(self) -> str:
        return f"Profile {self.profile} · {button_display_name(self.button)}"


@dataclass(frozen=True, slots=True)
class StartupReport:
    restored: tuple[int, ...]
    seeded: tuple[int, ...]
    problems: tuple[str, ...]


def button_display_name(button: int) -> str:
    names = {1: "Left Click", 2: "Right Click", 3: "Wheel Click", 4: "Fire Button",
             5: "DPI+ Button", 6: "DPI− Button", 19: "Wheel Up", 20: "Wheel Down"}
    if button in names:
        return names[button]
    if 7 <= button <= 18:
        return f"Thumb {button - 6}"
    return f"Button {button}"


#: PFD GROUP keys edited by the Lighting page (sent to the mouse live).
LIGHTING_FIELDS = ("LedMode1", "LedColor1", "LedState1", "BreathState1", "FlashState1")


class VenusWorkspace:
    """Single source of truth for the offline application state."""

    def __init__(self, store: AppStore, profiles: ProfileSetSession,
                 library: MacroLibrary) -> None:
        self.store = store
        self.profiles = profiles
        self.library = library
        self._listeners: list[Callable[[str], None]] = []
        self.startup = StartupReport((), (), ())
        # Per-slot bytes of the profile as it was last loaded, saved as or
        # restored; Cancel returns the slot to these bytes.
        self._loaded: dict[int, bytes] = {}
        #: (profile, button) re-pointed by the last slot operation (move, swap,
        #: remove); the window sends these live with the slot uploads (option b).
        self.slot_rewritten_buttons: set[tuple[int, int]] = set()
        #: (profile, button) whose record changed with a macro loop edit; sent live too.
        self.loop_rewritten_buttons: set[tuple[int, int]] = set()
        for slot in SLOTS:
            session = self.profiles.session_for_slot(slot)
            if session is not None:
                self._loaded[slot] = session.profile.to_bytes()

    # ------------------------------------------------------------ startup
    @classmethod
    def open(cls, store: AppStore | None = None) -> "VenusWorkspace":
        store = store or AppStore()
        state = {}
        problems: list[str] = []
        try:
            state = store.load_state()
        except StoreError as exc:
            problems.append(str(exc))
        selected = state.get("selected_slot", 1)
        if selected not in SLOTS:
            selected = 1
        profiles = ProfileSetSession(selected_slot=1)
        restored: list[int] = []
        seeded: list[int] = []
        for slot in SLOTS:
            path = store.profile_path(slot)
            session = None
            if path.exists():
                try:
                    session = ProfileDocumentSession.load(path)
                except ProfileLoadError as exc:
                    problems.append(f"Profile {slot} could not be restored ({exc}); factory settings loaded")
                else:
                    origin = state.get("origins", {}).get(str(slot)) if isinstance(state.get("origins"), dict) else None
                    session.origin = Path(origin) if origin else None
                    restored.append(slot)
            if session is None:
                session = _factory_session(slot, path)
                try:
                    session.save_owned(store)
                except (ProfileSaveError, OSError) as exc:
                    problems.append(f"Profile {slot} factory settings could not be stored: {exc}")
                seeded.append(slot)
            profiles.install_session(slot, session)
        profiles.select_slot(selected)
        try:
            library = MacroLibrary.open(store)
        except (StoreError, MacroLibraryError) as exc:
            problems.append(f"Macro library could not be read: {exc}")
            library = MacroLibrary(store)
        problems.extend(library.load_notes)
        workspace = cls(store, profiles, library)
        workspace.startup = StartupReport(tuple(restored), tuple(seeded), tuple(problems))
        return workspace

    # ------------------------------------------------------------ events
    def subscribe(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def notify(self, reason: str) -> None:
        for listener in tuple(self._listeners):
            listener(reason)

    # ------------------------------------------------------------ state
    @property
    def selected_slot(self) -> int:
        return self.profiles.selected_slot

    def select_slot(self, slot: int) -> None:
        if slot == self.profiles.selected_slot:
            return
        self.profiles.select_slot(slot)
        self.notify("selection")

    def profile(self, slot: int | None = None) -> PfdProfile:
        session = self.profiles.session_for_slot(slot or self.selected_slot)
        if session is None:
            raise ProfileLoadError("profile slot is empty")
        return session.profile

    def session(self, slot: int | None = None) -> ProfileDocumentSession:
        session = self.profiles.session_for_slot(slot or self.selected_slot)
        if session is None:
            raise ProfileLoadError("profile slot is empty")
        return session

    def unsaved_profile_slots(self) -> tuple[int, ...]:
        """Slots whose working content differs from the stored file."""
        result = []
        for slot in SLOTS:
            session = self.profiles.session_for_slot(slot)
            if session is None:
                continue
            if session.dirty or not self.store.profile_path(slot).exists():
                result.append(slot)
        return tuple(result)

    def is_profile_dirty(self, slot: int) -> bool:
        return slot in self.unsaved_profile_slots()

    @property
    def macros_dirty(self) -> bool:
        return self.library.dirty

    @property
    def dirty(self) -> bool:
        return bool(self.unsaved_profile_slots()) or self.macros_dirty

    def edited(self, reason: str = "edit") -> None:
        """Call after any in-memory profile edit made through ``profile()``."""
        self.notify(reason)

    # ------------------------------------------------------------ saving
    def save_all(self) -> tuple[str, ...]:
        """Persist every unsaved profile and macro change atomically per file.

        Profiles are written first, so an unfinished macro (which the library
        refuses to save) never holds back profile edits.
        """
        saved: list[str] = []
        for slot in self.unsaved_profile_slots():
            session = self.session(slot)
            session.path = self.store.profile_path(slot)
            session.save_owned(self.store)
            saved.append(f"profile {slot}")
        macro_error: Exception | None = None
        if self.library.dirty:
            try:
                self.library.save()
            except (MacroLibraryError, StoreError) as exc:
                macro_error = exc
            else:
                saved.append("macros")
        self._save_state()
        self.notify("saved")
        if macro_error is not None:
            raise ProfileSaveError(f"Macros could not be saved: {macro_error}") from macro_error
        return tuple(saved)

    def _save_state(self) -> None:
        origins = {}
        for slot in SLOTS:
            session = self.profiles.session_for_slot(slot)
            if session is not None and session.origin is not None:
                origins[str(slot)] = str(session.origin)
        try:
            self.store.save_state({"selected_slot": self.selected_slot, "origins": origins})
        except StoreError:
            pass

    def remember_selection(self) -> None:
        self._save_state()

    def validation_problems(self) -> list[str]:
        """Non-blocking warnings shown to the user (Windows-style rules)."""
        problems = []
        for slot in SLOTS:
            session = self.profiles.session_for_slot(slot)
            if session is None:
                continue
            if not bf.left_click_retained(session.profile):
                problems.append(f"Profile {slot} has no Left Click button; at least one is required")
            for reference in self.macro_references(slot):
                macro = self.library.slot_macro(reference.slot)
                if macro is None:
                    problems.append(
                        f"{reference.description} uses empty macro slot {reference.slot + 1}"
                    )
        return problems

    # ------------------------------------------------------------ revert
    def revert_profile(self, slot: int) -> None:
        session = self.session(slot)
        if self.store.profile_path(slot).exists():
            session.revert()
        else:
            self.profiles.install_session(slot, _factory_session(slot, self.store.profile_path(slot)))
        self.notify("revert")

    def revert_all(self) -> None:
        for slot in SLOTS:
            if self.is_profile_dirty(slot):
                self.revert_profile(slot)
        self.library.revert()
        self.notify("revert")

    # ------------------------------------------------------------ loaded profile
    def _mark_loaded(self, slot: int) -> None:
        self._loaded[slot] = self.session(slot).profile.to_bytes()

    def edited_since_load(self, slot: int | None = None) -> bool:
        """True when the slot differs from the profile it last loaded."""
        target = slot or self.selected_slot
        loaded = self._loaded.get(target)
        return loaded is not None and self.session(target).profile.to_bytes() != loaded

    def edited_slots(self) -> tuple[int, ...]:
        return tuple(slot for slot in SLOTS
                     if self.profiles.session_for_slot(slot) is not None and self.edited_since_load(slot))

    def loaded_name(self, slot: int | None = None) -> str:
        """Display name of the profile a slot holds: a file stem or Factory."""
        origin = self.session(slot or self.selected_slot).origin
        if origin is None or origin.name.startswith("factory "):
            return "Factory profile"
        if origin.name == FROM_MOUSE_ORIGIN:
            return "From mouse"
        return origin.stem

    def load_profile(self, path: str | Path, *, slot: int | None = None) -> ProfileDocumentSession:
        """*Load File*: copy a PFD into a slot; the source file is never changed.

        Any edits in the slot are replaced (the GUI asks first).
        """
        target = slot or self.selected_slot
        session = self.import_profile(path, slot=target, confirm_replace_dirty=True)
        self._mark_loaded(target)
        self.notify("load")
        return session

    def save_profile_as(self, destination: str | Path, *, slot: int | None = None,
                        replace_existing: bool = False) -> Path:
        """*Save As*: write one slot to a named, portable PFD file.

        ``replace_existing`` must be passed explicitly (after the save dialog
        confirmed the overwrite) to replace an existing file. The slot then
        holds that named profile, so Cancel returns to what was just saved.
        """
        target = slot or self.selected_slot
        previous = self.profiles.selected_slot
        self.profiles.select_slot(target)
        try:
            written = self.profiles.export_copy(destination, portable=True,
                                                replace_existing=replace_existing)
        finally:
            self.profiles.select_slot(previous)
        self.session(target).origin = Path(written)
        self._mark_loaded(target)
        self._save_state()
        self.notify("save-as")
        return written

    def cancel_edits(self, slot: int | None = None) -> None:
        """*Cancel*: discard edits since the slot's profile was loaded."""
        target = slot or self.selected_slot
        loaded = self._loaded.get(target)
        if loaded is None or not self.edited_since_load(target):
            return
        origin = self.session(target).origin
        previous = self.profiles.selected_slot
        self.profiles.select_slot(target)
        try:
            self.profiles.import_bytes_into_selected(
                loaded, origin or Path(f"factory RSProfile{target}.pfd"),
                confirm_replace_dirty=True, keep_baseline=True,
            )
        finally:
            self.profiles.select_slot(previous)
        self.session(target).origin = origin
        self.notify("cancel")

    # ------------------------------------------------------------ device read
    def apply_device_read(self, decoded):
        """The mouse wins: adopt a decoded read of the mouse (``device_decode``).

        Changed slots are labelled "From mouse"; every slot's loaded baseline
        becomes the mouse's state, so Cancel returns to what the mouse holds.
        Returns the ``ApplyReport`` (changes and notices).
        """
        from .device_decode import apply_to_workspace

        report = apply_to_workspace(self, decoded)
        for slot in report.profiles_changed:
            self.session(slot).origin = Path(FROM_MOUSE_ORIGIN)
        for slot in SLOTS:
            if self.profiles.session_for_slot(slot) is not None:
                self._mark_loaded(slot)
        self._save_state()
        self.notify("device-read")
        return report

    def commit_lighting(self, slot: int) -> bool:
        """Lighting was sent live: take it into the slot's Cancel baseline.

        Only the lighting fields change in the baseline, so lighting edits are
        not shown as pending (they are already on the mouse) while other edits
        in the slot keep their marker. Returns True if the baseline changed.
        """
        loaded = self._loaded.get(slot)
        if loaded is None:
            return False
        current = self.session(slot).profile
        baseline = PfdProfile.from_bytes(loaded)
        current_group, baseline_group = current.group_section, baseline.group_section
        if current_group is None or baseline_group is None:
            return False
        for key in LIGHTING_FIELDS:
            try:
                baseline.document.set(baseline_group, key, current.document.require(current_group, key))
            except (KeyError, ValueError):
                continue
        committed = current.to_bytes() if baseline.to_bytes() == current.to_bytes() else baseline.to_bytes()
        if committed == loaded:
            return False
        self._loaded[slot] = committed
        self._save_state()
        self.notify("lighting-committed")
        return True

    def adopt_desktop_settings(self, values) -> bool:
        """The desktop wins: copy its pointer speed / acceleration / double-click into every profile.

        ``values`` is a ``desktop_settings.DesktopValues``; unavailable (None)
        settings are left alone.  Each slot's Cancel baseline takes the same
        values, so this is never counted as an edit and other edits are kept.
        Returns True if any profile changed.
        """
        from .desktop_settings import acceleration_level_for

        def adopt(profile: PfdProfile) -> None:
            if values.pointer_level is not None and profile.pointer_speed_raw != values.pointer_level:
                profile.set_pointer_speed(values.pointer_level)
            if values.acceleration_on is not None:
                level = acceleration_level_for(values.acceleration_on, profile.acceleration)
                if profile.acceleration_raw != level:
                    profile.set_acceleration(level)
            if values.double_click_level is not None and \
                    profile.double_click_speed_raw != values.double_click_level:
                profile.set_double_click_speed(values.double_click_level)

        changed = False
        for slot in SLOTS:
            session = self.profiles.session_for_slot(slot)
            if session is None:
                continue
            before = session.profile.to_bytes()
            adopt(session.profile)
            loaded = self._loaded.get(slot)
            if loaded is not None:
                baseline = PfdProfile.from_bytes(loaded)
                adopt(baseline)
                self._loaded[slot] = baseline.to_bytes()
                if self.session(slot).profile.to_bytes() == self._loaded[slot]:
                    self._loaded[slot] = session.profile.to_bytes()   # identical: keep exact bytes
            changed |= session.profile.to_bytes() != before
        if changed:
            self.notify("desktop-settings")
        return changed

    def mark_applied(self) -> None:
        """Apply succeeded: every slot's Cancel baseline becomes its current state.

        The edited markers clear and Cancel returns to what was applied, as in
        Windows, where Apply commits the edits.
        """
        for slot in SLOTS:
            if self.profiles.session_for_slot(slot) is not None:
                self._mark_loaded(slot)
        self._save_state()
        self.notify("applied")

    def apply_mouse_dpi_stage(self, slot: int, stage: int) -> bool:
        """The mouse wins: a DPI± press on the mouse selected ``stage`` (zero-based).

        Only the DPI stage changes. The slot's Cancel baseline takes the same
        stage, so the press is not counted as an edit and other edits in the
        slot are kept. Returns False when the slot already had that stage.
        """
        profile = self.profile(slot)
        if profile.selected_dpi_stage_raw == stage:
            return False
        was_edited = self.edited_since_load(slot)
        profile.set_selected_dpi_stage(stage + 1)
        if slot in self._loaded:
            if was_edited:
                baseline = PfdProfile.from_bytes(self._loaded[slot])
                baseline.set_selected_dpi_stage(stage + 1)
                self._loaded[slot] = baseline.to_bytes()
            else:
                self._mark_loaded(slot)
        self.session(slot).origin = Path(FROM_MOUSE_ORIGIN)
        self._save_state()
        self.notify("mouse-dpi-stage")
        return True

    # ------------------------------------------------------------ import/export
    def import_profile(self, path: str | Path, *, slot: int | None = None,
                       confirm_replace_dirty: bool = False) -> ProfileDocumentSession:
        """Import any compatible PFD into a slot as an unsaved change."""
        target = slot or self.selected_slot
        previous = self.profiles.selected_slot
        self.profiles.select_slot(target)
        try:
            session = self.profiles.import_into_selected(
                path, confirm_replace_dirty=confirm_replace_dirty, keep_baseline=True,
            )
        finally:
            self.profiles.select_slot(previous)
        self.notify("import")
        return session

    def export_profile(self, destination: str | Path, *, portable: bool,
                       slot: int | None = None) -> Path:
        target = slot or self.selected_slot
        previous = self.profiles.selected_slot
        self.profiles.select_slot(target)
        try:
            return self.profiles.export_copy(destination, portable=portable)
        finally:
            self.profiles.select_slot(previous)

    def restore_defaults(self, slot: int) -> None:
        """Replace one profile with its shipped factory settings (unsaved)."""
        session = self.session(slot)
        previous = self.profiles.selected_slot
        self.profiles.select_slot(slot)
        try:
            self.profiles.import_bytes_into_selected(
                factory_profile_bytes(slot), Path(f"factory RSProfile{slot}.pfd"),
                confirm_replace_dirty=True, keep_baseline=True,
            )
        finally:
            self.profiles.select_slot(previous)
        self.session(slot).origin = None
        del session
        self._mark_loaded(slot)
        self.notify("restore")

    def reset_all(self) -> None:
        for slot in SLOTS:
            self.restore_defaults(slot)

    # ------------------------------------------------------------ macros ↔ buttons
    def macro_references(self, slot: int | None = None) -> tuple[MacroReference, ...]:
        """Every button referencing a device macro slot, in the given profile(s)."""
        result = []
        for profile_slot in (SLOTS if slot is None else (slot,)):
            session = self.profiles.session_for_slot(profile_slot)
            if session is None:
                continue
            for button in range(1, bf.BUTTON_COUNT + 1):
                view = bf.read_assignment(session.profile, button)
                if view.function_code == bf.FUNCTION_MACRO:
                    result.append(MacroReference(profile_slot, button, view.key_number, view.macro_name))
        return tuple(result)

    def references_to_slot(self, slot: int) -> tuple[MacroReference, ...]:
        return tuple(item for item in self.macro_references() if item.slot == slot)

    def reference_mismatches(self) -> tuple[str, ...]:
        """Buttons whose recorded MacroName disagrees with the bank slot."""
        notes = []
        for reference in self.macro_references():
            macro = self.library.slot_macro(reference.slot) if 0 <= reference.slot < 15 else None
            if macro is None:
                notes.append(f"{reference.description}: slot {reference.slot + 1} is empty "
                             f"(profile expects {reference.macro_name or 'a macro'!r})")
            elif macro.name != reference.macro_name:
                notes.append(f"{reference.description}: expects {reference.macro_name!r} but slot "
                             f"{reference.slot + 1} holds {macro.name!r}")
        return tuple(notes)

    def assign_macro_to_button(self, slot_index: int, profile_slot: int, button: int) -> None:
        macro = self.library.slot_macro(slot_index)
        if macro is None:
            raise MacroLibraryError(f"device slot {slot_index + 1} is empty")
        bf.set_macro(self.profile(profile_slot), button, slot_index, macro.name)
        self.notify("edit")

    def _rewrite_references(self, mapping: Callable[[MacroReference], tuple[str, int, str] | None]) -> int:
        """Apply a rewrite to every macro reference.

        ``mapping`` returns ``("macro", slot, name)``, ``("disable", 0, "")`` or
        None (unchanged).
        """
        changed = 0
        for reference in self.macro_references():
            outcome = mapping(reference)
            if outcome is None:
                continue
            kind, new_slot, new_name = outcome
            profile = self.profile(reference.profile)
            if kind == "disable":
                bf.set_simple_function(profile, reference.button, bf.FUNCTION_DISABLE)
            else:
                bf.set_macro(profile, reference.button, new_slot, new_name)
            if (kind, new_slot) != ("macro", reference.slot):     # the device record changes (not just a name)
                self.slot_rewritten_buttons.add((reference.profile, reference.button))
            changed += 1
        return changed

    def mark_macro_loop_changed(self, macro_id: str) -> None:
        """A macro's loop mode/count changed: its buttons' device records change.

        The loop flags and count live in each button's ``91`` record (hid.exe
        0x412F10), so the buttons pointing at the macro's device slot are queued
        like a slot move and follow live. A macro not loaded in a slot has no
        buttons on the mouse.
        """
        slot = self.library.bank.slot_of(macro_id)
        if slot is None:
            return
        for reference in self.references_to_slot(slot):
            self.loop_rewritten_buttons.add((reference.profile, reference.button))

    def take_slot_rewritten_buttons(self) -> set[tuple[int, int]]:
        keys, self.slot_rewritten_buttons = self.slot_rewritten_buttons, set()
        return keys

    def take_loop_rewritten_buttons(self) -> set[tuple[int, int]]:
        keys, self.loop_rewritten_buttons = self.loop_rewritten_buttons, set()
        return keys

    def commit_buttons(self, keys) -> bool:
        """Button records sent live with a slot move: take them into the Cancel baseline.

        Only those buttons' sections change in each slot's baseline, so they
        are not shown as pending while other edits keep their marker.
        """
        changed = False
        by_slot: dict[int, list[int]] = {}
        for profile_slot, button in keys:
            by_slot.setdefault(profile_slot, []).append(button)
        for profile_slot, buttons in by_slot.items():
            loaded = self._loaded.get(profile_slot)
            if loaded is None:
                continue
            current = self.session(profile_slot).profile
            baseline = PfdProfile.from_bytes(loaded)
            for button in buttons:
                section = bf.button_section(current, button)
                target = bf.button_section(baseline, button)
                for key, value in current.document.items(section):
                    baseline.document.set(target, key, value)
            committed = current.to_bytes() if baseline.to_bytes() == current.to_bytes() else baseline.to_bytes()
            if committed != loaded:
                self._loaded[profile_slot] = committed
                changed = True
        if changed:
            self._save_state()
            self.notify("buttons-committed")
        return changed

    def load_macro_into_slot(self, macro_id: str, slot: int) -> int:
        """Load a library macro into a slot.

        Buttons follow the slot (device semantics): every button that used
        the slot now runs the new macro, so their ``MacroName`` is updated.
        If the macro moved from another slot, buttons that used it follow it.
        Returns the number of button references rewritten.
        """
        entry = self.library.entry(macro_id)
        old_slot = self.library.bank.slot_of(macro_id)
        self.library.load_into_slot(macro_id, slot)

        def mapping(reference: MacroReference):
            if reference.slot == slot:
                return ("macro", slot, entry.name)
            if old_slot is not None and reference.slot == old_slot:
                return ("macro", slot, entry.name)
            return None

        changed = self._rewrite_references(mapping)
        self.notify("macros")
        return changed

    def unload_slot(self, slot: int) -> int:
        """Empty a slot; buttons that used it are set to Disable."""
        self.library.unload_slot(slot)
        changed = self._rewrite_references(
            lambda reference: ("disable", 0, "") if reference.slot == slot else None
        )
        self.notify("macros")
        return changed

    def swap_slots(self, first: int, second: int) -> int:
        """Exchange slots; buttons keep running the same macro."""
        if first == second:
            return 0
        self.library.swap_slots(first, second)

        def mapping(reference: MacroReference):
            if reference.slot == first:
                return ("macro", second, reference.macro_name)
            if reference.slot == second:
                return ("macro", first, reference.macro_name)
            return None

        changed = self._rewrite_references(mapping)
        self.notify("macros")
        return changed

    def rename_macro(self, macro_id: str, name: str) -> int:
        entry = self.library.rename(macro_id, name)
        slot = self.library.bank.slot_of(macro_id)
        changed = 0
        if slot is not None:
            changed = self._rewrite_references(
                lambda reference: ("macro", slot, entry.name) if reference.slot == slot else None
            )
        self.notify("macros")
        return changed

    def delete_macro(self, macro_id: str) -> int:
        slot = self.library.bank.slot_of(macro_id)
        self.library.delete(macro_id)
        changed = 0
        if slot is not None:
            changed = self._rewrite_references(
                lambda reference: ("disable", 0, "") if reference.slot == slot else None
            )
        self.notify("macros")
        return changed

    def affected_by_slot_change(self, slot: int) -> tuple[MacroReference, ...]:
        return self.references_to_slot(slot)


def _factory_session(slot: int, store_path: Path) -> ProfileDocumentSession:
    """Factory profile for a slot, held as an unsaved session at the store path."""
    profiles = ProfileSetSession(selected_slot=slot)
    session = profiles.import_bytes_into_selected(
        factory_profile_bytes(slot), Path(f"factory RSProfile{slot}.pfd"),
    )
    session.path = store_path
    session.profile.source_path = store_path
    session.origin = None
    return session
