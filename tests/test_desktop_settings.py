"""Desktop pointer speed / acceleration / double-click (offline: fake GSettings, D-Bus, kdeglobals)."""

from pathlib import Path

import pytest

from venus_profile import desktop_settings as ds
from venus_profile.desktop_settings import DesktopValues


# ----------------------------------------------------------------- mappings


def test_pointer_speed_mapping_keeps_the_windows_default_at_zero():
    assert ds.pointer_level_to_speed(10) == 0
    assert ds.pointer_level_to_speed(1) == -1
    assert ds.pointer_level_to_speed(20) == 1
    for level in range(1, 21):
        assert ds.speed_to_pointer_level(ds.pointer_level_to_speed(level)) == level
    assert ds.speed_to_pointer_level(0.35) == 14 and ds.speed_to_pointer_level(-5) == 1


def test_double_click_uses_the_windows_formula():
    """hid.exe 0x414EC7: SetDoubleClickTime(900 - level * 70)."""
    assert [ds.double_click_level_to_ms(level) for level in (1, 5, 10)] == [830, 550, 200]
    for level in range(1, 11):
        assert ds.ms_to_double_click_level(ds.double_click_level_to_ms(level)) == level
    assert ds.ms_to_double_click_level(400) == 7 and ds.ms_to_double_click_level(5000) == 1


def test_acceleration_off_is_level_1_and_on_keeps_a_stored_strength():
    """hid.exe 0x4109d0: level 1 sets MouseSpeed 0 (off); 2..4 turn it on."""
    assert ds.acceleration_level_for(False, 3) == 1
    assert ds.acceleration_level_for(True, 3) == 3
    assert ds.acceleration_level_for(True, 1) == 2 and ds.acceleration_level_for(True, None) == 2


# ----------------------------------------------------------------- detection


def test_detection_follows_the_session_and_the_kill_switch():
    assert ds.detect_desktop({"XDG_CURRENT_DESKTOP": "KDE", ds.DISABLE_ENV: "1"}) is None
    assert ds.detect_desktop({"XDG_CURRENT_DESKTOP": "XFCE"}) is None
    assert ds.detect_desktop({}) is None


# ----------------------------------------------------------------- GNOME


class _Schema:
    def __init__(self, keys):
        self.keys = keys

    def has_key(self, key):
        return key in self.keys


class FakeGSettings:
    def __init__(self, values, locked=()):
        self.values = dict(values)
        self.locked = set(locked)

    def get_property(self, name):
        assert name == "settings-schema"
        return _Schema(self.values)

    def is_writable(self, key):
        return key not in self.locked

    def get_double(self, key):
        return self.values[key]

    get_int = get_string = get_double

    def _set(self, key, value):
        if key in self.locked:
            return False
        self.values[key] = value
        return True

    set_double = set_int = set_string = _set


def test_gnome_reads_and_writes_its_mouse_keys():
    fake = FakeGSettings({"speed": 0.0, "accel-profile": "default", "double-click": 400})
    gnome = ds.GnomeDesktop(fake)
    assert gnome.read() == DesktopValues(10, True, 7)          # 'default' = the device default (adaptive)
    gnome.write(pointer_level=15, acceleration_on=False, double_click_level=5)
    assert fake.values == {"speed": 0.5, "accel-profile": "flat", "double-click": 550}


def test_gnome_hides_locked_or_missing_keys_and_reports_refusals():
    fake = FakeGSettings({"speed": 0.0, "accel-profile": "flat"}, locked={"speed"})
    gnome = ds.GnomeDesktop(fake)
    assert gnome.read() == DesktopValues(None, False, None)
    with pytest.raises(ds.DesktopSettingsError):
        gnome.write(pointer_level=12)


# ----------------------------------------------------------------- KDE


class FakeKWinBus:
    """Answers org.freedesktop.DBus.Properties calls like KWin's InputDevice objects."""

    def __init__(self, devices, fail_set=False):
        self.devices = devices          # sysname -> properties
        self.fail_set = fail_set
        self.signals = []

    def call_sync(self, service, path, interface, method, args, reply_type, flags, timeout, cancellable):
        from gi.repository import GLib
        assert service == "org.kde.KWin" and interface == "org.freedesktop.DBus.Properties"
        unpacked = args.unpack()
        if method == "Get":
            assert unpacked == ("org.kde.KWin.InputDeviceManager", "devicesSysNames")
            return GLib.Variant("(v)", (GLib.Variant("as", list(self.devices)),))
        props = self.devices[path.rsplit("/", 1)[1]]
        if method == "GetAll":
            assert unpacked == ("org.kde.KWin.InputDevice",)
            return GLib.Variant("(a{sv})", ({key: _variant(value) for key, value in props.items()},))
        if method == "Set":
            if self.fail_set:
                raise RuntimeError("org.freedesktop.DBus.Error.AccessDenied")
            _iface, name, value = unpacked
            props[name] = value
            if name == "pointerAccelerationProfileAdaptive":
                props["pointerAccelerationProfileFlat"] = not value
            if name == "pointerAccelerationProfileFlat":
                props["pointerAccelerationProfileAdaptive"] = not value
            return GLib.Variant("()", ())
        raise AssertionError(method)

    def emit_signal(self, destination, path, interface, name, params):
        self.signals.append((path, interface, name, params.unpack()))


def _variant(value):
    from gi.repository import GLib
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    if isinstance(value, int):
        return GLib.Variant("u", value)
    return GLib.Variant("s", value)


def _venus(**extra):
    props = {"name": "USB Gaming Mouse", "vendor": 0x04D9, "product": 0xFC55, "pointer": True,
             "supportsPointerAcceleration": True, "supportsPointerAccelerationProfileFlat": True,
             "supportsPointerAccelerationProfileAdaptive": True, "pointerAcceleration": 0.0,
             "pointerAccelerationProfileFlat": False, "pointerAccelerationProfileAdaptive": True}
    props.update(extra)
    return props


def test_kde_reads_and_writes_only_the_venus_pointer(tmp_path):
    other = _venus(name="Other mouse", vendor=0x046D, product=0xC077, pointerAcceleration=0.7)
    keyboard = _venus(name="USB Gaming Mouse Keyboard", pointer=False)
    bus = FakeKWinBus({"event3": other, "event5": keyboard, "event6": _venus(pointerAcceleration=-0.2)})
    kde = ds.KdeDesktop(bus, tmp_path)
    assert kde.read() == DesktopValues(8, True, 7)              # no kdeglobals key: KDE's 400 ms default
    kde.write(pointer_level=20, acceleration_on=False)
    venus = bus.devices["event6"]
    assert venus["pointerAcceleration"] == 1.0 and venus["pointerAccelerationProfileFlat"] is True
    assert other["pointerAcceleration"] == 0.7 and other["pointerAccelerationProfileFlat"] is False
    assert kde.read().acceleration_on is False


def test_kde_without_the_mouse_offers_only_double_click(tmp_path):
    kde = ds.KdeDesktop(FakeKWinBus({}), tmp_path)
    assert kde.read() == DesktopValues(None, None, 7)
    with pytest.raises(ds.DesktopSettingsError, match="plugged in"):
        kde.write(pointer_level=12)


def test_kde_refusal_is_reported(tmp_path):
    kde = ds.KdeDesktop(FakeKWinBus({"event6": _venus()}, fail_set=True), tmp_path)
    with pytest.raises(ds.DesktopSettingsError):
        kde.write(acceleration_on=False)


KDEGLOBALS = """[General]
ColorScheme=BreezeDark
fixed=Hack,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1

[KDE]
LookAndFeelPackage=org.kde.breezedark.desktop
SingleClick=false

[KDE Action Restrictions][$i]
action/kwin_rmb=false
"""


def test_kde_double_click_edits_kdeglobals_and_keeps_every_other_line(tmp_path):
    (tmp_path / "kdeglobals").write_text(KDEGLOBALS)
    (tmp_path / "kdeglobals").chmod(0o640)
    bus = FakeKWinBus({})
    kde = ds.KdeDesktop(bus, tmp_path)
    kde.write(double_click_level=5)
    text = (tmp_path / "kdeglobals").read_text()
    assert text == KDEGLOBALS.replace("SingleClick=false\n", "SingleClick=false\nDoubleClickInterval=550\n")
    assert (tmp_path / "kdeglobals").stat().st_mode & 0o777 == 0o640
    assert kde.read().double_click_level == 5
    kde.write(double_click_level=10)                            # an existing key is replaced in place
    assert (tmp_path / "kdeglobals").read_text().count("DoubleClickInterval=") == 1
    assert "DoubleClickInterval=200\n" in (tmp_path / "kdeglobals").read_text()
    changed = [params for path, iface, name, params in bus.signals
               if (path, iface, name) == ("/kdeglobals", "org.kde.kconfig.notify", "ConfigChanged")]
    assert changed and [bytes(key) for key in changed[-1][0]["KDE"]] == [b"DoubleClickInterval"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["kdeglobals"]   # no temp files left


@pytest.mark.parametrize("text, expected", [
    ("", "[KDE]\nDoubleClickInterval=480\n"),
    ("[General]\nA=1", "[General]\nA=1\n\n[KDE]\nDoubleClickInterval=480\n"),
    ("[KDE]\nA=1", "[KDE]\nA=1\nDoubleClickInterval=480\n"),
    ("[KDE]\nDoubleClickInterval = 300\n[Other]\n", "[KDE]\nDoubleClickInterval=480\n[Other]\n"),
    ("[KDE][Sub]\nDoubleClickInterval=300\n", "[KDE][Sub]\nDoubleClickInterval=300\n\n[KDE]\nDoubleClickInterval=480\n"),
])
def test_kdeglobals_edit_cases(text, expected):
    assert ds.set_kde_double_click(text, 480) == expected


@pytest.mark.parametrize("text", [
    "[KDE]\nDoubleClickInterval[$i]=400\n",
    "[KDE][$i]\nDoubleClickInterval=400\n",
    "[$i]\n[KDE]\n",
])
def test_locked_kde_double_click_is_hidden(tmp_path, text):
    (tmp_path / "kdeglobals").write_text(text)
    kde = ds.KdeDesktop(FakeKWinBus({}), tmp_path)
    assert kde.read().double_click_level is None
    with pytest.raises(ds.DesktopSettingsError):
        kde.write(double_click_level=5)
    assert (tmp_path / "kdeglobals").read_text() == text


# ----------------------------------------------------------------- workspace


def test_adopting_the_desktop_changes_every_profile_without_an_edit():
    from gui_workspace import temp_workspace
    workspace = temp_workspace()
    workspace.profile(2).set_acceleration(3)
    workspace.mark_applied()
    workspace.profile(1).set_scroll_speed(7)                  # an unrelated edit is kept
    assert workspace.adopt_desktop_settings(DesktopValues(14, True, 7))
    for slot in range(1, 6):
        profile = workspace.profile(slot)
        assert (profile.pointer_speed, profile.double_click_speed) == (14, 7)
        assert profile.acceleration == (3 if slot == 2 else 2)
    assert workspace.edited_slots() == (1,)
    workspace.cancel_edits(1)
    assert workspace.profile(1).pointer_speed == 14 and workspace.edited_slots() == ()
    assert not workspace.adopt_desktop_settings(DesktopValues(14, True, 7))
    assert workspace.adopt_desktop_settings(DesktopValues(None, False, None))
    assert workspace.profile(2).acceleration == 1 and workspace.profile(2).pointer_speed == 14
