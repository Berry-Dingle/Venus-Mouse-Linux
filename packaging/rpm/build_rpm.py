"""Build the Venus Mouse rpm (one noarch package for Fedora 44, 45 and later).

    python3 packaging/rpm/build_rpm.py [--output DIR]

Makes the source tarball from the files git tracks (their current contents),
then runs ``rpmbuild`` in the fixed folder ``/tmp/venus-mouse-rpmbuild`` (so no
checkout path or user name is recorded) and copies the packages to ``build/rpm``. The
result has no dist tag (``venus-mouse-<version>-1.noarch.rpm``) because the same
package installs on every supported Fedora release. Timestamps come from the
last commit, so rebuilding the same tree gives the same payload.

Needs ``rpmbuild`` (rpm-build), ``desktop-file-utils`` and ``systemd-rpm-macros``.
The build never runs the app, never installs anything and never touches a device.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
SPEC = HERE / "venus-mouse.spec"
NAME = "venus-mouse"
#: rpmbuild writes its build paths into the .src.rpm (the %prep/%install script
#: text), so it always runs in this fixed, neutral folder: no user name or
#: checkout path is recorded, and every checkout gives the same packages.
TOPDIR = Path("/tmp/venus-mouse-rpmbuild")


def spec_version() -> str:
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        if line.startswith("Version:"):
            return line.split()[1]
    raise SystemExit("no Version: in the spec")


def project_version() -> str:
    for line in (PROJECT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=PROJECT, check=True, capture_output=True).stdout
    return sorted(name for name in out.decode().split("\0") if name and (PROJECT / name).is_file())


def source_epoch() -> int:
    out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=PROJECT, check=True,
                         capture_output=True, text=True).stdout.strip()
    return int(out)


def make_tarball(target: Path, version: str, epoch: int) -> Path:
    """A deterministic tar.gz of the tracked files under ``venus-mouse-<version>/``."""
    prefix = f"{NAME}-{version}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in tracked_files():
            path = PROJECT / name
            info = tarfile.TarInfo(f"{prefix}/{name}")
            data = path.read_bytes()
            info.size = len(data)
            info.mtime = epoch
            info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=epoch, filename="") as gz:
            gz.write(raw.getvalue())
    return target


def build(output: Path) -> Path:
    version = spec_version()
    if version != project_version():
        raise SystemExit(f"spec Version {version} != pyproject version {project_version()}")
    if shutil.which("rpmbuild") is None:
        raise SystemExit("rpmbuild not found (install rpm-build)")
    epoch = source_epoch()
    top = TOPDIR
    if top.exists():
        shutil.rmtree(top)
    stale = output / "rpmbuild"                 # where older versions of this script built
    if stale.exists():
        shutil.rmtree(stale)
    make_tarball(top / "SOURCES" / f"{NAME}-{version}.tar.gz", version, epoch)
    defines = {
        "_topdir": str(top),
        "dist": "%{nil}",
        "source_date_epoch_from_changelog": "0",
        "clamp_mtime_to_source_date_epoch": "1",
        "use_source_date_epoch_as_buildtime": "1",
        "_buildhost": "reproducible",
    }
    command = ["rpmbuild", "-ba", "--quiet"]
    for key, value in defines.items():
        command += ["--define", f"{key} {value}"]
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    # rpmbuild records the spec's file time in the .src.rpm (not clamped when it
    # is older than SOURCE_DATE_EPOCH), so build from a copy stamped with the
    # commit time: the same commit then gives the same packages in any checkout.
    spec = top / "SPECS" / SPEC.name
    spec.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SPEC, spec)
    os.utime(spec, (epoch, epoch))
    subprocess.run(command + [str(spec)], check=True, env=env)
    built = sorted((top / "RPMS" / "noarch").glob(f"{NAME}-{version}-*.noarch.rpm"))
    if len(built) != 1:
        raise SystemExit(f"expected one noarch rpm, found {built}")
    rpm = output / built[0].name
    shutil.copy2(built[0], rpm)
    for srpm in (top / "SRPMS").glob("*.src.rpm"):
        shutil.copy2(srpm, output / srpm.name)
    shutil.rmtree(top)
    return rpm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=PROJECT / "build/rpm")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    rpm = build(args.output.resolve())
    print(f"built    {rpm}  ({rpm.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
