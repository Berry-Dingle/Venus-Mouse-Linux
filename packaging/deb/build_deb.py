"""Build the Venus Mouse .deb (one ``Architecture: all`` package for Debian 13+ / Ubuntu 24.04+).

    python3 packaging/deb/build_deb.py [--output DIR]

The payload is the same as the rpm's (``packaging/install_tree.py``: the app in
``/usr/share/venus-mouse``, run by ``/usr/bin/python3``, so it does not depend
on a particular Python version). Debian metadata added here: ``DEBIAN/control``,
``DEBIAN/postinst`` (creates the optional ``venus-mouse`` group, reloads udev
rules), ``DEBIAN/md5sums``, and ``/usr/share/doc/venus-mouse/`` (``copyright``,
``changelog.Debian.gz``, ``README.md``). Timestamps come from the last commit,
so the same tree builds a byte-identical package.

Needs ``dpkg-deb``. The build never installs anything, never runs the app and
never touches a device.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "packaging"))
import install_tree  # noqa: E402  (the one definition of the payload)

NAME = "venus-mouse"
REVISION = "1"
MAINTAINER = "Berry Dingle <dzll-launcher@tutamail.com>"
HOMEPAGE = "https://github.com/Berry-Dingle/Venus-Mouse-Linux"
#: Debian/Ubuntu package names for what the rpm requires (versions = upstream versions).
DEPENDS = (
    "python3 (>= 3.11)",
    "python3-gi",
    "gir1.2-gtk-4.0 (>= 4.10)",
    "gir1.2-adw-1 (>= 1.5)",
    "hicolor-icon-theme",
)
DESCRIPTION = """\
Description: configure the UtechSmart Venus MMO mouse
 Venus Mouse configures the wired UtechSmart Venus MMO gaming mouse
 (USB 04d9:fc55) on Linux: button assignments, DPI stages, polling rate,
 scroll speed, lighting, macros and the five on-mouse profiles.
 .
 The package installs a udev rule that gives the logged-in desktop user
 access to the mouse's configuration interface only (no root access
 needed), and an optional "venus-mouse" system group for non-desktop access.
"""


def project_version() -> str:
    for line in (PROJECT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def deb_version() -> str:
    return f"{project_version()}-{REVISION}"


def source_epoch() -> int:
    out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=PROJECT, check=True,
                         capture_output=True, text=True).stdout.strip()
    return int(out)


def copyright_text() -> str:
    """Machine-readable debian/copyright (DEP-5); the OFL is not in common-licenses, so it is included."""
    ofl = (PROJECT / "src/venus_profile/gui/data/fonts/AlexBrush-OFL.txt").read_text(encoding="utf-8")
    ofl_body = "\n".join(f" {line}" if line.strip() else " ." for line in ofl.strip().splitlines())
    return f"""\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Venus Mouse
Upstream-Contact: {MAINTAINER}
Source: {HOMEPAGE}

Files: *
Copyright: 2026 Berry Dingle
License: GPL-3.0+

Files: usr/share/venus-mouse/venus_profile/gui/data/fonts/*
Copyright: 2011 The Alex Brush Project Authors (https://github.com/googlefonts/alex-brush)
License: OFL-1.1

License: GPL-3.0+
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
 .
 On Debian systems, the complete text of the GNU General Public License
 version 3 can be found in "/usr/share/common-licenses/GPL-3".

License: OFL-1.1
{ofl_body}
"""


def changelog_text(epoch: int) -> str:
    stamp = subprocess.run(["date", "-u", "-R", "-d", f"@{epoch}"], check=True, capture_output=True,
                           text=True).stdout.strip()
    return (f"{NAME} ({deb_version()}) unstable; urgency=medium\n\n"
            f"  * First deb: one Architecture: all package for Debian 13+ and Ubuntu 24.04+.\n\n"
            f" -- {MAINTAINER}  {stamp}\n")


def control_text(installed_kib: int) -> str:
    return (f"Package: {NAME}\n"
            f"Version: {deb_version()}\n"
            f"Architecture: all\n"
            f"Maintainer: {MAINTAINER}\n"
            f"Installed-Size: {installed_kib}\n"
            f"Depends: {', '.join(DEPENDS)}\n"
            f"Section: utils\n"
            f"Priority: optional\n"
            f"Homepage: {HOMEPAGE}\n"
            + DESCRIPTION)


def stage(root: Path, epoch: int) -> None:
    """The full package tree (payload + doc + DEBIAN/) under ``root``."""
    if root.exists():
        shutil.rmtree(root)
    payload = install_tree.install(root)
    doc = root / "usr/share/doc" / NAME
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "copyright").write_text(copyright_text(), encoding="utf-8")
    shutil.copyfile(PROJECT / "README.md", doc / "README.md")
    with open(doc / "changelog.Debian.gz", "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=epoch, filename="", compresslevel=9) as gz:
            gz.write(changelog_text(epoch).encode())
    for path in doc.iterdir():
        os.chmod(path, 0o644)
    os.chmod(doc, 0o755)

    files = sorted(payload + [str(p.relative_to(root)) for p in doc.iterdir()])
    sums = "".join(f"{hashlib.md5((root / f).read_bytes()).hexdigest()}  {f}\n" for f in files)
    installed = sum((root / f).stat().st_size for f in files)
    debian = root / "DEBIAN"
    debian.mkdir()
    (debian / "control").write_text(control_text((installed + 1023) // 1024), encoding="utf-8")
    (debian / "md5sums").write_text(sums, encoding="utf-8")
    shutil.copyfile(HERE / "postinst", debian / "postinst")
    os.chmod(debian, 0o755)
    for name, mode in (("control", 0o644), ("md5sums", 0o644), ("postinst", 0o755)):
        os.chmod(debian / name, mode)
    for path in [root, *root.rglob("*")]:
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def build(output: Path) -> Path:
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb not found (install dpkg)")
    epoch = source_epoch()
    root = output / "root"
    stage(root, epoch)
    deb = output / f"{NAME}_{deb_version()}_all.deb"
    deb.unlink(missing_ok=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(deb)],
                   check=True, env=env, stdout=subprocess.DEVNULL)
    shutil.rmtree(root)
    return deb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=PROJECT / "build/deb")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    deb = build(args.output.resolve())
    print(f"built    {deb}  ({deb.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256   {hashlib.sha256(deb.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
