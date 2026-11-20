"""Application-owned persistent storage for the Linux Venus configurator.

The Windows application keeps one working PFD per profile slot
(``RSProfile1.pfd`` .. ``RSProfile5.pfd``) plus a ``MacroDB`` folder.  The
Linux application mirrors that model with files it owns exclusively:

``<root>/profiles/profile<N>.pfd``   five working profiles
``<root>/macros/library/<id>.MSMACRO`` unlimited local macro library
``<root>/macros/library.json``        library index (names, order)
``<root>/macros/bank.json``           the 15 active device-slot references
``<root>/state.json``                 small UI state (selected slot)

``<root>`` is ``$VENUS_PROFILE_DATA_DIR`` when set, otherwise
``$XDG_DATA_HOME/venus-profile`` (``~/.local/share/venus-profile``).

Only files below ``<root>`` may be replaced in place.  Imported user files
and exports are handled elsewhere with no-clobber semantics.  This module has
no device access.
"""

from __future__ import annotations

from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile

FACTORY_PACKAGE = "venus_profile.data.factory"
DATA_DIR_ENV = "VENUS_PROFILE_DATA_DIR"
PROFILE_LIBRARY_ENV = "VENUS_PROFILE_LIBRARY_DIR"
MACRO_LIBRARY_ENV = "VENUS_MACRO_LIBRARY_DIR"
STATE_VERSION = 1


class StoreError(OSError):
    """An application-store read or write failed."""


def default_root() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "venus-profile"


def _venus_documents_dir() -> Path:
    """``~/Documents/VenusMouse`` (XDG Documents directory when available)."""
    documents = None
    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        documents = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
    except (ImportError, ValueError, AttributeError):
        documents = None
    base = Path(documents) if documents else Path.home() / "Documents"
    return base / "VenusMouse"


def default_profile_library_dir() -> Path:
    """Where users keep named profiles: ``~/Documents/VenusMouse/Profiles``.

    This is a user folder (not application-owned): files there are written
    only through an explicit Save As, and never replaced without the save
    dialog's overwrite confirmation. ``$VENUS_PROFILE_LIBRARY_DIR`` overrides
    it (used by tests); otherwise the XDG Documents directory is used.
    """
    override = os.environ.get(PROFILE_LIBRARY_ENV)
    if override:
        return Path(override).expanduser()
    return _venus_documents_dir() / "Profiles"


def default_macro_library_dir() -> Path:
    """The macro library: ``~/Documents/VenusMouse/Macros``.

    One ``<name>.MSMACRO`` per macro plus ``MacroSet.MSDB`` (the 15 device
    slots), in the Windows formats, so copying ``Documents/VenusMouse`` to
    another computer carries the library. ``$VENUS_MACRO_LIBRARY_DIR``
    overrides it (used by tests).
    """
    override = os.environ.get(MACRO_LIBRARY_ENV)
    if override:
        return Path(override).expanduser()
    return _venus_documents_dir() / "Macros"


def factory_profile_bytes(number: int) -> bytes:
    """Return the shipped Windows factory profile ``RSProfile<number>.pfd``."""
    if isinstance(number, bool) or not isinstance(number, int) or number not in range(1, 6):
        raise ValueError("factory profile number must be 1..5")
    return (files(FACTORY_PACKAGE) / f"RSProfile{number}.pfd").read_bytes()


class AppStore:
    """Atomic, root-confined storage for application-owned files."""

    def __init__(self, root: str | Path | None = None, *,
                 macro_library_dir: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()
        # The default store uses the user's Documents library; a store opened
        # at an explicit root (tests, previews) gets its own library folder.
        if macro_library_dir is not None:
            self.macro_library_dir = Path(macro_library_dir)
        elif root is None:
            self.macro_library_dir = default_macro_library_dir()
        else:
            self.macro_library_dir = self.root / "VenusMouse" / "Macros"

    # ------------------------------------------------------------- layout
    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"

    @property
    def macros_dir(self) -> Path:
        return self.root / "macros"

    @property
    def library_dir(self) -> Path:
        return self.macros_dir / "library"

    @property
    def deleted_macros_dir(self) -> Path:
        return self.macros_dir / "deleted"

    @property
    def library_index_path(self) -> Path:
        return self.macros_dir / "library.json"

    @property
    def bank_path(self) -> Path:
        return self.macros_dir / "bank.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def profile_path(self, slot: int) -> Path:
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in range(1, 6):
            raise ValueError("profile slot must be 1..5")
        return self.profiles_dir / f"profile{slot}.pfd"

    # ------------------------------------------------------------- safety
    def owns(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        return True

    def _require_owned(self, path: Path) -> Path:
        if not self.owns(path):
            raise StoreError(f"refusing to write outside the application store: {path}")
        return path

    # ------------------------------------------------------------- I/O
    def write_bytes(self, path: str | Path, payload: bytes) -> Path:
        """Atomically replace an application-owned file."""
        target = self._require_owned(Path(path))
        temp_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
            temp_name = None
        except OSError as exc:
            raise StoreError(f"could not write {target}: {exc}") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
        return target

    def write_json(self, path: str | Path, value: object) -> Path:
        text = json.dumps(value, indent=2, sort_keys=True) + "\n"
        return self.write_bytes(path, text.encode("utf-8"))

    def read_json(self, path: str | Path, default: object = None) -> object:
        source = Path(path)
        if not source.exists():
            return default
        try:
            return json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError(f"could not read {source}: {exc}") from exc

    def move_to(self, source: str | Path, destination: str | Path) -> Path:
        origin = self._require_owned(Path(source))
        target = self._require_owned(Path(destination))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise StoreError(f"destination already exists: {target}")
            os.replace(origin, target)
        except OSError as exc:
            raise StoreError(f"could not move {origin}: {exc}") from exc
        return target

    def load_state(self) -> dict:
        value = self.read_json(self.state_path, {})
        return value if isinstance(value, dict) else {}

    def save_state(self, state: dict) -> None:
        payload = dict(state)
        payload["version"] = STATE_VERSION
        self.write_json(self.state_path, payload)

