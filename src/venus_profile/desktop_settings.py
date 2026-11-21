"""Pointer speed, acceleration and double-click speed as desktop settings.

The Windows utility applies these three profile settings to the PC, never to
the mouse (``hid.exe`` 0x4109d0: ``SPI_SETMOUSE`` acceleration,
``SPI_SETMOUSESPEED`` pointer speed; 0x414EC7 ``SetDoubleClickTime``).  On
Linux they map to the desktop's own mouse settings:

* GNOME: GSettings ``org.gnome.desktop.peripherals.mouse`` keys ``speed``,
  ``accel-profile`` and ``double-click`` (they apply to every mouse);
* KDE Plasma (Wayland): KWin's ``org.kde.KWin.InputDevice`` D-Bus properties
  of this mouse (04D9:FC55) for speed and acceleration, as Plasma's own Mouse
  page does (KWin stores them in ``kcminputrc``), and ``kdeglobals``
  ``[KDE] DoubleClickInterval`` for double-click, as Plasma's Workspace page.

No mouse access happens here: this only talks to the desktop.  Setting
``VENUS_PROFILE_NO_DESKTOP_SETTINGS=1`` turns it off (tests set it).

Mappings (Windows level <-> Linux value):

* pointer speed 1..20 (Windows default 10) <-> libinput speed -1..1, with 10
  at 0; linear on each side (1 -> -1, 20 -> +1).  Inference: Windows and
  libinput curves differ, so equal positions do not feel identical;
* acceleration 1 = off (0x4109d0 sets ``MouseSpeed`` 0); 2..4 = on with
  rising thresholds.  libinput has only flat (off) and adaptive (on), so
  levels 2..4 all mean adaptive;
* double-click level 1..10 <-> ``900 - 70 * level`` ms (830..200 ms).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

DISABLE_ENV = "VENUS_PROFILE_NO_DESKTOP_SETTINGS"
VENUS_VENDOR_ID = 0x04D9
VENUS_PRODUCT_ID = 0xFC55

POINTER = "pointer"
ACCELERATION = "acceleration"
DOUBLE_CLICK = "double_click"
ALL_SETTINGS = (POINTER, ACCELERATION, DOUBLE_CLICK)

POINTER_LEVEL_MIN, POINTER_LEVEL_MAX, POINTER_LEVEL_DEFAULT = 1, 20, 10
DOUBLE_CLICK_LEVEL_MIN, DOUBLE_CLICK_LEVEL_MAX = 1, 10


class DesktopSettingsError(RuntimeError):
    """The desktop refused or could not take a setting."""


# ----------------------------------------------------------------- mappings


def pointer_level_to_speed(level: int) -> float:
    if not POINTER_LEVEL_MIN <= level <= POINTER_LEVEL_MAX:
        raise ValueError("pointer speed level must be 1..20")
    offset = level - POINTER_LEVEL_DEFAULT
    return round(offset / 9 if offset <= 0 else offset / 10, 3)


def speed_to_pointer_level(speed: float) -> int:
    speed = min(max(float(speed), -1.0), 1.0)
    level = POINTER_LEVEL_DEFAULT + (speed * 9 if speed <= 0 else speed * 10)
    return int(min(max(round(level), POINTER_LEVEL_MIN), POINTER_LEVEL_MAX))


def double_click_level_to_ms(level: int) -> int:
    if not DOUBLE_CLICK_LEVEL_MIN <= level <= DOUBLE_CLICK_LEVEL_MAX:
        raise ValueError("double-click level must be 1..10")
    return 900 - 70 * level


def ms_to_double_click_level(ms: int) -> int:
    level = round((900 - int(ms)) / 70)
    return int(min(max(level, DOUBLE_CLICK_LEVEL_MIN), DOUBLE_CLICK_LEVEL_MAX))


def acceleration_level_for(on: bool, current_level: int | None) -> int:
    """The profile level for on/off: off is 1; on keeps a stored 2..4, else 2."""
    if not on:
        return 1
    return current_level if current_level in (2, 3, 4) else 2


@dataclass(frozen=True, slots=True)
class DesktopValues:
    """The desktop's current values as profile levels; None = not available."""

    pointer_level: int | None = None
    acceleration_on: bool | None = None
    double_click_level: int | None = None

    @property
    def available(self) -> frozenset[str]:
        names = []
        if self.pointer_level is not None:
            names.append(POINTER)
        if self.acceleration_on is not None:
            names.append(ACCELERATION)
        if self.double_click_level is not None:
            names.append(DOUBLE_CLICK)
        return frozenset(names)


# ----------------------------------------------------------------- GNOME

GNOME_MOUSE_SCHEMA = "org.gnome.desktop.peripherals.mouse"


class GnomeDesktop:
    name = "GNOME"
    scope_note = "GNOME applies these to every mouse."

    def __init__(self, settings=None) -> None:
        self._settings = settings if settings is not None else self._open()

    @staticmethod
    def _open():
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(GNOME_MOUSE_SCHEMA, True) if source is not None else None
        if schema is None:            # Gio.Settings.new would abort the process
            return None
        return Gio.Settings.new_full(schema, None, None)

    def _has(self, key: str) -> bool:
        settings = self._settings
        if settings is None:
            return False
        schema = settings.get_property("settings-schema")
        return schema is not None and schema.has_key(key) and settings.is_writable(key)

    def read(self) -> DesktopValues:
        s = self._settings
        return DesktopValues(
            pointer_level=speed_to_pointer_level(s.get_double("speed")) if self._has("speed") else None,
            acceleration_on=(s.get_string("accel-profile") != "flat") if self._has("accel-profile") else None,
            double_click_level=ms_to_double_click_level(s.get_int("double-click"))
            if self._has("double-click") else None,
        )

    def write(self, *, pointer_level=None, acceleration_on=None, double_click_level=None) -> None:
        s = self._settings
        ok = True
        if pointer_level is not None:
            ok &= self._has("speed") and s.set_double("speed", pointer_level_to_speed(pointer_level))
        if acceleration_on is not None:
            ok &= self._has("accel-profile") and s.set_string(
                "accel-profile", "adaptive" if acceleration_on else "flat")
        if double_click_level is not None:
            ok &= self._has("double-click") and s.set_int(
                "double-click", double_click_level_to_ms(double_click_level))
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        Gio.Settings.sync()
        if not ok:
            raise DesktopSettingsError("GNOME refused a mouse setting (it may be locked)")


# ----------------------------------------------------------------- KDE

KWIN_SERVICE = "org.kde.KWin"
KWIN_DEVICES_PATH = "/org/kde/KWin/InputDevice"
KWIN_MANAGER_IFACE = "org.kde.KWin.InputDeviceManager"
KWIN_DEVICE_IFACE = "org.kde.KWin.InputDevice"
KDE_DOUBLE_CLICK_GROUP = "KDE"
KDE_DOUBLE_CLICK_KEY = "DoubleClickInterval"
KDE_DOUBLE_CLICK_DEFAULT_MS = 400          # KDE's default (kcm_workspace)
DBUS_TIMEOUT_MS = 1000


def kde_config_dir(env=None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path(env.get("HOME", str(Path.home()))) / ".config"


_GROUP_LINE = re.compile(r"^\s*\[(.*)\]\s*$")


def _group_of(line: str) -> str | None:
    match = _GROUP_LINE.match(line)
    return match.group(1) if match else None


def read_kde_double_click(text: str) -> tuple[int | None, bool]:
    """(interval ms or None if unset, writable) from kdeglobals text."""
    group = None
    value = None
    for line in text.splitlines():
        header = _group_of(line)
        if header is not None:
            group = header
            if line.strip() == "[$i]":                    # whole file immutable
                return value, False
            if group in (f"{KDE_DOUBLE_CLICK_GROUP}][$i",):
                return value, False
            continue
        if group != KDE_DOUBLE_CLICK_GROUP or "=" not in line:
            continue
        key, _sep, raw = line.partition("=")
        key = key.strip()
        if key == f"{KDE_DOUBLE_CLICK_KEY}[$i]":
            return None, False
        if key == KDE_DOUBLE_CLICK_KEY:
            try:
                value = int(raw.strip())
            except ValueError:
                value = None
    return value, True


def set_kde_double_click(text: str, ms: int) -> str:
    """kdeglobals text with ``[KDE] DoubleClickInterval=ms``; every other line kept exactly."""
    lines = text.splitlines(keepends=True)
    group = None
    group_end = None          # index after the last line of the [KDE] group
    for index, line in enumerate(lines):
        header = _group_of(line)
        if header is not None:
            group = header
            if header == KDE_DOUBLE_CLICK_GROUP:
                group_end = index + 1
            continue
        if group != KDE_DOUBLE_CLICK_GROUP:
            continue
        if line.strip():
            group_end = index + 1
        key = line.partition("=")[0].strip()
        if "=" in line and key == KDE_DOUBLE_CLICK_KEY:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{KDE_DOUBLE_CLICK_KEY}={ms}{newline}"
            return "".join(lines)
    entry = f"{KDE_DOUBLE_CLICK_KEY}={ms}\n"
    if group_end is not None:
        if not lines[group_end - 1].endswith("\n"):
            lines[group_end - 1] += "\n"
        lines.insert(group_end, entry)
        return "".join(lines)
    prefix = "".join(lines)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return f"{prefix}[{KDE_DOUBLE_CLICK_GROUP}]\n{entry}"


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class KdeDesktop:
    name = "KDE Plasma"
    scope_note = "Pointer speed and acceleration apply to this mouse only, as in Plasma's Mouse settings."

    def __init__(self, bus=None, config_dir: Path | None = None) -> None:
        self._bus = bus if bus is not None else self._session_bus()
        self._config_dir = config_dir if config_dir is not None else kde_config_dir()

    @staticmethod
    def _session_bus():
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        try:
            return Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:                                 # no session bus
            return None

    # -- KWin (speed, acceleration)
    def _call(self, path: str, method: str, args, reply: str):
        from gi.repository import Gio, GLib
        result = self._bus.call_sync(KWIN_SERVICE, path, "org.freedesktop.DBus.Properties", method, args,
                                     GLib.VariantType.new(reply), Gio.DBusCallFlags.NONE, DBUS_TIMEOUT_MS, None)
        return result.unpack()

    def _venus_devices(self) -> list[tuple[str, dict]]:
        """KWin pointer devices of this mouse model: [(object path, properties)]."""
        if self._bus is None:
            return []
        from gi.repository import GLib
        try:
            (names,) = self._call(KWIN_DEVICES_PATH, "Get",
                                  GLib.Variant("(ss)", (KWIN_MANAGER_IFACE, "devicesSysNames")), "(v)")
        except Exception:                                 # not KDE Wayland / KWin not reachable
            return []
        found = []
        for name in names or ():
            path = f"{KWIN_DEVICES_PATH}/{name}"
            try:
                (props,) = self._call(path, "GetAll", GLib.Variant("(s)", (KWIN_DEVICE_IFACE,)), "(a{sv})")
            except Exception:
                continue
            if (props.get("vendor") == VENUS_VENDOR_ID and props.get("product") == VENUS_PRODUCT_ID
                    and props.get("pointer") and props.get("supportsPointerAcceleration")):
                found.append((path, props))
        return found

    def _set(self, path: str, prop: str, value) -> None:
        from gi.repository import GLib
        self._call(path, "Set", GLib.Variant("(ssv)", (KWIN_DEVICE_IFACE, prop, value)), "()")

    # -- kdeglobals (double-click)
    @property
    def _kdeglobals(self) -> Path:
        return self._config_dir / "kdeglobals"

    def _read_kdeglobals(self) -> str:
        try:
            return self._kdeglobals.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read(self) -> DesktopValues:
        devices = self._venus_devices()
        pointer = acceleration = None
        if devices:
            props = devices[0][1]
            pointer = speed_to_pointer_level(props.get("pointerAcceleration", 0.0))
            if props.get("supportsPointerAccelerationProfileFlat") and \
                    props.get("supportsPointerAccelerationProfileAdaptive"):
                acceleration = not props.get("pointerAccelerationProfileFlat", False)
        double_click = None
        try:
            ms, writable = read_kde_double_click(self._read_kdeglobals())
        except (OSError, UnicodeDecodeError):
            ms, writable = None, False
        if writable:
            double_click = ms_to_double_click_level(ms if ms is not None else KDE_DOUBLE_CLICK_DEFAULT_MS)
        return DesktopValues(pointer, acceleration, double_click)

    def write(self, *, pointer_level=None, acceleration_on=None, double_click_level=None) -> None:
        from gi.repository import GLib
        if pointer_level is not None or acceleration_on is not None:
            devices = self._venus_devices()
            if not devices:
                raise DesktopSettingsError("KDE did not list the Venus mouse (is it plugged in?)")
            try:
                for path, _props in devices:
                    if pointer_level is not None:
                        self._set(path, "pointerAcceleration",
                                  GLib.Variant("d", pointer_level_to_speed(pointer_level)))
                    if acceleration_on is not None:
                        prop = "pointerAccelerationProfileAdaptive" if acceleration_on \
                            else "pointerAccelerationProfileFlat"
                        self._set(path, prop, GLib.Variant("b", True))
            except Exception as exc:
                raise DesktopSettingsError(f"KDE refused the pointer setting ({exc})") from exc
        if double_click_level is not None:
            ms = double_click_level_to_ms(double_click_level)
            try:
                text = self._read_kdeglobals()
                if not read_kde_double_click(text)[1]:
                    raise DesktopSettingsError("KDE's double-click setting is locked")
                _atomic_write(self._kdeglobals, set_kde_double_click(text, ms))
            except (OSError, UnicodeDecodeError) as exc:
                raise DesktopSettingsError(f"kdeglobals could not be written ({exc})") from exc
            self._announce_double_click()

    def _announce_double_click(self) -> None:
        """Tell running KDE apps, as Plasma's own settings page does (best effort)."""
        if self._bus is None:
            return
        from gi.repository import GLib
        try:
            self._bus.emit_signal(None, "/kdeglobals", "org.kde.kconfig.notify", "ConfigChanged",
                                  GLib.Variant("(a{saay})", ({KDE_DOUBLE_CLICK_GROUP: [KDE_DOUBLE_CLICK_KEY.encode()]},)))
            # KGlobalSettings::notifyChange(SettingsChanged = 3, SETTINGS_MOUSE = 0)
            self._bus.emit_signal(None, "/KGlobalSettings", "org.kde.KGlobalSettings", "notifyChange",
                                  GLib.Variant("(ii)", (3, 0)))
        except Exception:
            pass


# ----------------------------------------------------------------- detection


def desktop_settings_disabled(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get(DISABLE_ENV, "") not in ("", "0")


def detect_desktop(env=None):
    """The backend for this session's desktop, or None (unsupported or disabled)."""
    env = os.environ if env is None else env
    if desktop_settings_disabled(env):
        return None
    desktops = {part.strip().casefold() for part in env.get("XDG_CURRENT_DESKTOP", "").split(":")}
    try:
        if "kde" in desktops:
            return KdeDesktop()
        if "gnome" in desktops:
            backend = GnomeDesktop()
            return backend if backend._settings is not None else None
    except Exception:
        return None
    return None
