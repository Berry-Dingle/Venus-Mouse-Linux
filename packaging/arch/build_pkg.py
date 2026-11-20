"""Build the ready-to-install Arch package (``venus-mouse-<ver>-1-any.pkg.tar.zst``).

    python3 packaging/arch/build_pkg.py [--output DIR] [--source TARBALL]

Runs on Arch, or in an Arch container (for example ``distrobox enter <arch-box>
-- python3 packaging/arch/build_pkg.py``). It runs ``makepkg`` on the PKGBUILD,
which downloads the GitHub archive of tag ``v<pkgver>`` and checks its SHA-256,
so the tag must already be pushed. ``--source`` hands makepkg a local copy of
that archive instead (``git archive --format=tar --prefix=Venus-Mouse-Linux-<ver>/
v<ver> | gzip -n`` reproduces GitHub's file byte for byte); makepkg still checks
it against the PKGBUILD's SHA-256, so only the exact release archive builds. The package is the one ``makepkg -si`` would
build, for users who'd rather just ``sudo pacman -U`` it.

Reproducible: makepkg runs in the fixed folder ``/tmp/venus-mouse-archbuild``
(``.BUILDINFO`` records the build folder, so no user name or checkout path ends
up in the package), with ``SOURCE_DATE_EPOCH`` = the tag's commit time.
``-d`` skips makepkg's check that the runtime dependencies are installed: the
payload is plain Python, nothing is compiled, so the build doesn't need them.

The build never installs anything, never runs the app and never touches a device.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
PKGBUILD = HERE / "PKGBUILD"
PACKAGER = "Berry Dingle <dzll-launcher@tutamail.com>"
#: .BUILDINFO records builddir/startdir, so makepkg always runs here.
TOPDIR = Path("/tmp/venus-mouse-archbuild")


def pkgbuild_value(name: str) -> str:
    out = subprocess.run(["bash", "-c", f'source "{PKGBUILD}"; printf %s "${name}"'],
                         check=True, capture_output=True, text=True).stdout
    if not out:
        raise SystemExit(f"no {name} in the PKGBUILD")
    return out


def package_name() -> str:
    return f"venus-mouse-{pkgbuild_value('pkgver')}-{pkgbuild_value('pkgrel')}-any.pkg.tar.zst"


def tag_epoch(version: str) -> int:
    """Commit time of the release tag the PKGBUILD downloads."""
    out = subprocess.run(["git", "log", "-1", "--format=%ct", f"v{version}"], cwd=PROJECT, check=True,
                         capture_output=True, text=True).stdout.strip()
    return int(out)


def source_name() -> str:
    """File name makepkg looks for in SRCDEST (the ``name::url`` prefix of ``source``)."""
    return f"venus-mouse-{pkgbuild_value('pkgver')}.tar.gz"


def build(output: Path, source: Path | None = None) -> Path:
    if shutil.which("makepkg") is None:
        raise SystemExit("makepkg not found: run this on Arch or in an Arch container")
    epoch = tag_epoch(pkgbuild_value("pkgver"))
    shutil.rmtree(TOPDIR, ignore_errors=True)
    TOPDIR.mkdir(parents=True)
    try:
        shutil.copyfile(PKGBUILD, TOPDIR / "PKGBUILD")
        if source is not None:
            shutil.copyfile(source, TOPDIR / source_name())   # makepkg verifies its SHA-256
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "PACKAGER": PACKAGER,
               "PKGEXT": ".pkg.tar.zst", "BUILDDIR": str(TOPDIR), "PKGDEST": str(TOPDIR),
               "SRCDEST": str(TOPDIR), "SRCPKGDEST": str(TOPDIR)}
        subprocess.run(["makepkg", "-d", "--cleanbuild", "--noconfirm"], cwd=TOPDIR, env=env,
                       check=True, stdout=subprocess.DEVNULL)
        built = TOPDIR / package_name()
        result = output / built.name
        result.unlink(missing_ok=True)
        shutil.copyfile(built, result)
        os.utime(result, (epoch, epoch))
        return result
    finally:
        shutil.rmtree(TOPDIR, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=PROJECT / "build/arch")
    parser.add_argument("--source", type=Path, default=None,
                        help="local copy of the v<pkgver> release archive (checked by makepkg)")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    pkg = build(args.output.resolve(), args.source.resolve() if args.source else None)
    print(f"built    {pkg}  ({pkg.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256   {hashlib.sha256(pkg.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
