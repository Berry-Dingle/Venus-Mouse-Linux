"""Lay out the Venus Mouse package payload under a root (rpm %install, deb, Arch package()).

    python3 packaging/install_tree.py ROOT

The application files are exactly the AppImage's (``build_appimage.bundle_files``:
the import closure of the GUI, its three device helpers and the click counter,
plus their resources), installed into a private directory that does not depend
on the Python version (``/usr/share/venus-mouse``), so one architecture-independent
package works with any Python 3.11 or newer (Fedora rpm, Debian/Ubuntu deb, Arch). No ``.pyc`` files are written.

Also installed: the ``venus-mouse`` launcher, the desktop entry, the hicolor
icons, the udev rule and the sysusers.d entry for the ``venus-mouse`` group.
Licences are installed by each package format (rpm ``%license``, deb
``copyright``, Arch ``/usr/share/licenses``). Nothing here runs the app or
touches a device.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT / "packaging" / "appimage"))
import build_appimage  # noqa: E402  (the one definition of what the app needs)

APP_ID = build_appimage.APP_ID
#: Private module directory (Fedora Python appendix: an application's private modules).
APP_DIR = Path("usr/share/venus-mouse")
LAUNCHER = Path("usr/bin/venus-mouse")
DESKTOP = Path("usr/share/applications") / f"{APP_ID}.desktop"
ICON_ROOT = Path("usr/share/icons/hicolor")
UDEV_RULE = Path("usr/lib/udev/rules.d/70-utechsmart-venus.rules")
SYSUSERS = Path("usr/lib/sysusers.d/venus-mouse.conf")


def desktop_entry() -> str:
    """The shared desktop entry, launching the packaged ``venus-mouse`` command."""
    source = (PROJECT / "packaging" / f"{APP_ID}.desktop").read_text(encoding="utf-8")
    lines = source.splitlines()
    if lines.count("Exec=venus-profile-gui") != 1:
        raise SystemExit("desktop entry: expected exactly one 'Exec=venus-profile-gui' line")
    return "\n".join("Exec=venus-mouse" if line == "Exec=venus-profile-gui" else line for line in lines) + "\n"


def _copy(source: Path, target: Path, mode: int = 0o644) -> None:
    if not source.is_file():
        raise SystemExit(f"missing file for the package: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, mode)


def install(root: Path) -> list[str]:
    """Copy the payload into ``root``; return the installed paths (relative to root), sorted."""
    root = Path(root)
    installed: list[Path] = []
    for relative in build_appimage.bundle_files():
        target = root / APP_DIR / relative
        _copy(build_appimage.SRC / relative, target)
        installed.append(target)
    # The app adds its own icon folder to GTK's search path; keep it beside the code.
    icons = build_appimage.SRC / "venus_profile/gui/data/icons/hicolor"
    for size in build_appimage.ICON_SIZES:
        for base in (APP_DIR / "venus_profile/gui/data/icons/hicolor", ICON_ROOT):
            target = root / base / size / "apps" / f"{APP_ID}.png"
            _copy(icons / size / "apps" / f"{APP_ID}.png", target)
            installed.append(target)
    for base in (APP_DIR / "venus_profile/gui/data/icons/hicolor", ICON_ROOT):
        target = root / base / "scalable/apps" / f"{APP_ID}.svg"
        _copy(icons / "scalable/apps" / f"{APP_ID}.svg", target)
        installed.append(target)

    _copy(HERE / "venus-mouse", root / LAUNCHER, 0o755)
    _copy(PROJECT / "packaging/udev/70-utechsmart-venus.rules", root / UDEV_RULE)
    _copy(HERE / "venus-mouse.sysusers", root / SYSUSERS)
    desktop = root / DESKTOP
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_entry(), encoding="utf-8")
    os.chmod(desktop, 0o644)
    installed += [root / LAUNCHER, root / UDEV_RULE, root / SYSUSERS, desktop]

    for path in installed:
        for parent in path.relative_to(root).parents:
            if parent != Path("."):
                os.chmod(root / parent, 0o755)
    return sorted(str(path.relative_to(root)) for path in installed)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: install_tree.py ROOT")
    files = install(Path(args[0]))
    print(f"installed {len(files)} files into {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
