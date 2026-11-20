"""Offline, lossless user-profile file session for the GUI boundary."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Mapping

from .pfd import PfdProfile
from .validation import Issue, validate_pfd


class ProfileLoadError(ValueError):
    """A selected file could not be parsed or has blocking validation errors."""


class ProfileSaveError(OSError):
    """A profile could not be saved without replacing an existing file."""


class MissingProfileError(ProfileLoadError):
    """A requested slot or required profile structure is absent."""


class IncompatibleProfileError(ProfileLoadError):
    """A PFD's numbered structure does not match its assigned slot."""


class AmbiguousProfileError(ProfileLoadError):
    """A PFD has duplicate or multiple profile structures."""


class DirtyProfileReplacementError(ValueError):
    """Replacing a dirty slot requires an explicit caller confirmation."""


@dataclass(slots=True)
class ProfileDocumentSession:
    """A PFD kept in memory; disk writes happen only through ``save_as``."""

    profile: PfdProfile
    path: Path
    _saved_bytes: bytes
    origin: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "ProfileDocumentSession":
        selected = Path(path)
        try:
            profile = PfdProfile.from_path(selected)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProfileLoadError(f"Could not read PFD file: {exc}") from exc
        return cls._validated(profile)

    @classmethod
    def _validated(
        cls,
        profile: PfdProfile,
    ) -> "ProfileDocumentSession":
        try:
            issues = validate_pfd(profile)
        except (ValueError, KeyError) as exc:
            raise ProfileLoadError(f"Invalid PFD file: {exc}") from exc
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            details = "; ".join(issue.message for issue in errors)
            raise ProfileLoadError(f"Invalid PFD file: {details}")
        if profile.source_path is None:
            raise ProfileLoadError("PFD profile has no source path")
        return cls(profile, profile.source_path, profile.to_bytes())

    @property
    def issues(self) -> tuple[Issue, ...]:
        """Current validator output; warnings identify preserved unknown values."""
        return tuple(validate_pfd(self.profile))

    @property
    def dirty(self) -> bool:
        return self.profile.to_bytes() != self._saved_bytes

    @property
    def saved_bytes(self) -> bytes:
        """The bytes of the last saved (or loaded) baseline."""
        return self._saved_bytes

    def revert(self) -> None:
        """Discard in-memory edits and return to the saved baseline."""
        if not self._saved_bytes:
            raise ProfileLoadError("this profile has no saved baseline to revert to")
        profile = PfdProfile.from_bytes(self._saved_bytes)
        profile.source_path = self.path
        self.profile = profile

    def save_owned(self, store) -> Path:
        """Replace an application-owned file atomically and re-baseline.

        ``store`` must own ``self.path``; user files are never replaced here.
        """
        if not store.owns(self.path):
            raise ProfileSaveError(f"refusing to replace a file the application does not own: {self.path}")
        errors = [issue for issue in validate_pfd(self.profile) if issue.severity == "error"]
        if errors:
            raise ProfileSaveError("Profile is invalid: " + "; ".join(
                issue.message for issue in errors
            ))
        payload = self.profile.to_bytes()
        try:
            store.write_bytes(self.path, payload)
        except OSError as exc:
            raise ProfileSaveError(f"Could not save PFD file: {exc}") from exc
        self.profile.source_path = self.path
        self._saved_bytes = payload
        return self.path

    def save_as(self, destination: str | Path) -> Path:
        """Write lossless serialized bytes to a new path, never clobbering."""
        target = Path(destination)
        if target.exists():
            raise ProfileSaveError(f"Destination already exists: {target}")
        payload = self.profile.to_bytes()
        errors = [issue for issue in validate_pfd(self.profile)
                  if issue.severity == "error"]
        if errors:
            raise ProfileSaveError("Profile is invalid: " + "; ".join(
                issue.message for issue in errors
            ))
        temp_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.",
                                             dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link gives no-clobber semantics even if the destination
            # appears after the initial exists check.
            os.link(temp_name, target)
            os.unlink(temp_name)
            temp_name = None
        except OSError as exc:
            raise ProfileSaveError(f"Could not save PFD file: {exc}") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
        self.path = target
        self.profile.source_path = target
        self._saved_bytes = payload
        return target

    @staticmethod
    def _write_export(payload: bytes, destination: str | Path, *,
                      replace_existing: bool = False) -> Path:
        """Write validated export bytes atomically without changing a session.

        Existing files are never replaced unless the caller passes
        ``replace_existing=True``, which the GUI does only after the user has
        picked that file in a save dialog and confirmed the overwrite.
        """
        target = Path(destination)
        if target.exists() and replace_existing is not True:
            raise ProfileSaveError(f"Destination already exists: {target}")
        if target.is_dir():
            raise ProfileSaveError(f"Destination is a folder: {target}")
        temp_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if replace_existing is True:
                os.replace(temp_name, target)
            else:
                # Hard-link gives no-clobber semantics even if the
                # destination appears after the initial exists check.
                os.link(temp_name, target)
                os.unlink(temp_name)
            temp_name = None
        except OSError as exc:
            raise ProfileSaveError(f"Could not save PFD file: {exc}") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
        return target


class ProfileSetSession:
    """Five independent PFD documents, one for each logical mouse slot."""

    SLOT_NUMBERS = (1, 2, 3, 4, 5)

    def __init__(self, selected_slot: int = 1) -> None:
        self._validate_slot(selected_slot)
        self._selected_slot = selected_slot
        self._slots: dict[int, ProfileDocumentSession] = {}

    @staticmethod
    def _validate_slot(slot: int) -> None:
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in range(1, 6):
            raise ValueError("profile slot must be an integer from 1 through 5")

    @property
    def selected_slot(self) -> int:
        return self._selected_slot

    @property
    def slots(self) -> Mapping[int, ProfileDocumentSession]:
        """Read-only view of loaded slots; each session remains independently editable."""
        return MappingProxyType(self._slots)

    @property
    def loaded_slots(self) -> tuple[int, ...]:
        return tuple(slot for slot in self.SLOT_NUMBERS if slot in self._slots)

    @property
    def selected_session(self) -> ProfileDocumentSession | None:
        return self._slots.get(self._selected_slot)

    @property
    def dirty_slots(self) -> tuple[int, ...]:
        return tuple(slot for slot in self.SLOT_NUMBERS
                     if slot in self._slots and self._slots[slot].dirty)

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_slots)

    def session_for_slot(self, slot: int) -> ProfileDocumentSession | None:
        self._validate_slot(slot)
        return self._slots.get(slot)

    def select_slot(self, slot: int) -> ProfileDocumentSession | None:
        """Select a logical slot without dropping any slot's in-memory edits."""
        self._validate_slot(slot)
        self._selected_slot = slot
        return self._slots.get(slot)

    def load_into_selected(
        self,
        path: str | Path,
        *,
        confirm_replace_dirty: bool = False,
    ) -> ProfileDocumentSession:
        """Import a PFD into the selected slot; dirty replacement needs confirmation."""
        return self.import_into_selected(
            path, confirm_replace_dirty=confirm_replace_dirty
        )

    def import_into_selected(
        self,
        path: str | Path,
        *,
        confirm_replace_dirty: bool = False,
        keep_baseline: bool = False,
    ) -> ProfileDocumentSession:
        """Validate and remap a PFD's profile identity to the selected GUI slot.

        With ``keep_baseline`` the slot keeps its existing saved path and
        baseline, so the imported content is an unsaved change of that slot
        (the application-store workflow).  The source file is never written.
        """
        slot = self._selected_slot
        selected_path = Path(path)
        if not selected_path.is_file():
            raise MissingProfileError(f"PFD file is missing or not a file: {selected_path}")
        try:
            data = selected_path.read_bytes()
        except OSError as exc:
            raise ProfileLoadError(f"Could not read PFD file: {exc}") from exc
        return self.import_bytes_into_selected(
            data, selected_path, confirm_replace_dirty=confirm_replace_dirty,
            keep_baseline=keep_baseline,
        )

    def import_bytes_into_selected(
        self,
        data: bytes,
        origin: Path,
        *,
        confirm_replace_dirty: bool = False,
        keep_baseline: bool = False,
    ) -> ProfileDocumentSession:
        slot = self._selected_slot
        current = self._slots.get(slot)
        if (current is not None and current.dirty
                and confirm_replace_dirty is not True):
            raise DirtyProfileReplacementError(
                f"Profile slot {slot} has unsaved changes; explicit replacement "
                "confirmation is required"
            )
        try:
            profile = PfdProfile.from_bytes(data)
        except (UnicodeError, ValueError) as exc:
            raise ProfileLoadError(f"Could not read PFD file: {exc}") from exc
        profile.source_path = Path(origin)
        try:
            self._remap_profile_for_slot(profile, slot)
        except KeyError as exc:
            raise IncompatibleProfileError(f"PFD structure cannot be remapped: {exc}") from exc
        candidate = ProfileDocumentSession._validated(profile)
        candidate.origin = Path(origin)
        if keep_baseline and current is not None:
            candidate.path = current.path
            candidate._saved_bytes = current._saved_bytes
            candidate.profile.source_path = current.path
        self._slots[slot] = candidate
        return candidate

    def install_session(self, slot: int, session: ProfileDocumentSession) -> None:
        """Place an already validated session in a slot (startup restoration)."""
        self._validate_slot(slot)
        self._slots[slot] = session

    @staticmethod
    def _remap_profile_for_slot(profile: PfdProfile, slot: int | None) -> None:
        """Remap only validated section/key identities, preserving all values."""
        if slot is not None:
            ProfileSetSession._validate_slot(slot)
        sections = profile.document.sections
        group_sections = [name for name in sections
                          if name == "GROUP" or name.startswith("GROUP")]
        groups = [name for name in group_sections
                  if re.fullmatch(r"GROUP(?:[0-4])?", name)]
        if not groups:
            if group_sections:
                raise IncompatibleProfileError(
                    "PFD has an unsupported GROUP section name: "
                    + ", ".join(f"[{name}]" for name in group_sections)
                )
            raise MissingProfileError("PFD has no GROUP section")
        if len(groups) != 1 or len(group_sections) != 1:
            raise AmbiguousProfileError(
                f"PFD has {len(group_sections)} GROUP sections; expected exactly one"
            )
        source_group = groups[0]
        source_match = re.fullmatch(r"GROUP([0-4])?", source_group)
        source_index = source_match.group(1) if source_match else None

        button_sections = [name for name in sections
                           if name.startswith("ButtonAssigned")]
        if not button_sections:
            raise MissingProfileError("PFD has no numbered button sections")
        button_pattern = re.compile(r"ButtonAssigned([0-4])?_(\d+)")
        parsed_buttons: list[tuple[str, str | None, int]] = []
        for name in button_sections:
            match = button_pattern.fullmatch(name)
            if match is None:
                raise IncompatibleProfileError(
                    f"PFD has an unsupported button section name [{name}]"
                )
            number = int(match.group(2))
            if match.group(2) != str(number):
                raise IncompatibleProfileError(
                    f"PFD has a non-canonical button section name [{name}]"
                )
            parsed_buttons.append((name, match.group(1), number))
        button_names = [name for name, _, _ in parsed_buttons]
        duplicates = sorted({name for name in button_names
                             if button_names.count(name) > 1})
        if duplicates:
            raise AmbiguousProfileError(
                "PFD contains duplicate button sections: " + ", ".join(duplicates)
            )
        if any(source != source_index for _, source, _ in parsed_buttons):
            raise IncompatibleProfileError(
                f"PFD GROUP identity [{source_group}] does not match its "
                "ButtonAssigned section identities"
            )
        button_numbers = [number for _, _, number in parsed_buttons]
        if sorted(button_numbers) != list(range(1, 21)):
            missing = sorted(set(range(1, 21)) - set(button_numbers))
            unexpected = sorted(set(button_numbers) - set(range(1, 21)))
            if unexpected:
                raise IncompatibleProfileError(
                    "PFD has unsupported button numbers: "
                    + ", ".join(map(str, unexpected))
                )
            raise MissingProfileError(
                "PFD is missing numbered button sections: "
                + ", ".join(map(str, missing))
            )

        expected_keys = {f"ButtonAssigned{source_index or ''}_{number}"
                         for number in range(1, 21)}
        group_keys = profile.document.items(source_group)
        for scalar in ("GroupID", "Group"):
            if sum(key == scalar for key, _ in group_keys) > 1:
                raise AmbiguousProfileError(
                    f"[{source_group}] contains duplicate {scalar} fields"
                )
        identity_keys = [key for key, _ in group_keys
                         if key.startswith("ButtonAssigned")]
        if len(identity_keys) != len(set(identity_keys)):
            raise AmbiguousProfileError(
                f"[{source_group}] contains duplicate ButtonAssigned keys"
            )
        actual_keys = set(identity_keys)
        if actual_keys != expected_keys:
            unsupported = sorted(actual_keys - expected_keys)
            if unsupported:
                raise IncompatibleProfileError(
                    f"[{source_group}] has unsupported button references: "
                    + ", ".join(unsupported)
                )
            raise MissingProfileError(
                f"[{source_group}] is missing button data for "
                + ", ".join(sorted(expected_keys - actual_keys))
            )

        for name in expected_keys:
            if profile.document.get(source_group, name) is None:
                raise MissingProfileError(
                    f"[{source_group}] is missing button data for {name}"
                )

        target_index = str(slot - 1) if slot is not None else ""
        target_group = f"GROUP{target_index}"
        if source_group != target_group:
            profile.document.rename_section(source_group, target_group)
        for number in range(1, 21):
            old_section = f"ButtonAssigned{source_index or ''}_{number}"
            new_section = f"ButtonAssigned{target_index}_{number}"
            profile.document.rename_section(old_section, new_section)
            old_key = old_section
            new_key = new_section
            profile.document.rename_key(target_group, old_key, new_key)

        # GroupID and Group are profile data, not structural slot identities.
        # Preserve their source values without warning or inferring a mapping.

    def save_as(self, destination: str | Path) -> Path:
        """Save only the selected loaded slot, retaining no-overwrite behavior."""
        session = self._slots.get(self._selected_slot)
        if session is None:
            raise MissingProfileError(
                f"Profile slot {self._selected_slot} has no loaded PFD"
            )
        return session.save_as(destination)

    def export_copy(self, destination: str | Path, *, portable: bool = True,
                    replace_existing: bool = False) -> Path:
        """Export a copy; never changes the loaded path, bytes, or dirty baseline."""
        session = self._slots.get(self._selected_slot)
        if session is None:
            raise MissingProfileError(
                f"Profile slot {self._selected_slot} has no loaded PFD"
            )
        profile = deepcopy(session.profile)
        if portable:
            self._remap_profile_for_slot(profile, None)
        errors = [issue for issue in validate_pfd(profile)
                  if issue.severity == "error"]
        if errors:
            raise ProfileSaveError("Profile is invalid: " + "; ".join(
                issue.message for issue in errors
            ))
        return ProfileDocumentSession._write_export(
            profile.to_bytes(), destination, replace_existing=replace_existing,
        )
