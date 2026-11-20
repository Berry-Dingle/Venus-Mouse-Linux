"""The Arch PKGBUILD: arch=any, shared payload, .SRCINFO in step. makepkg itself is not run here."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

PROJECT = Path(__file__).parents[1]
ARCH_DIR = PROJECT / "packaging" / "arch"
PKGBUILD = ARCH_DIR / "PKGBUILD"
REPO_URL = "https://github.com/Berry-Dingle/Venus-Mouse-Linux"


def _pkgbuild_vars() -> dict[str, list[str]]:
    names = ("pkgname", "pkgver", "pkgrel", "pkgdesc", "arch", "url", "license", "depends", "source",
             "sha256sums", "install", "makedepends", "backup")
    script = f"source {PKGBUILD}\n" + "".join(
        f'printf "%s\\0" "{name}" "${{#{name}[@]}}" "${{{name}[@]}}"\n' for name in names)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    parts = out.split("\0")
    result, index = {}, 0
    while index < len(parts) - 1:
        name, count = parts[index], int(parts[index + 1])
        result[name] = parts[index + 2:index + 2 + count]
        index += 2 + count
    return result


def _project_version() -> str:
    for line in (PROJECT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise AssertionError("no version")


def test_pkgbuild_is_arch_any_with_version_independent_python():
    v = _pkgbuild_vars()
    assert v["pkgname"] == ["venus-mouse"] and v["arch"] == ["any"]
    assert v["pkgver"] == [_project_version()] and v["pkgrel"] == ["1"]
    assert v["url"] == [REPO_URL]
    assert v["license"] == ["GPL-3.0-or-later", "OFL-1.1"]
    assert v["depends"] == ["python>=3.11", "python-gobject", "gtk4>=4.10", "libadwaita>=1.5",
                            "hicolor-icon-theme"]
    assert v["source"] == [f"venus-mouse-{_project_version()}.tar.gz::{REPO_URL}/archive/refs/tags/"
                           f"v{_project_version()}.tar.gz"]
    assert v["install"] == [] and v["makedepends"] == [] and v["backup"] == []   # no install scriptlet


def test_srcinfo_matches_the_pkgbuild():
    v = _pkgbuild_vars()
    expected = ["pkgbase = venus-mouse",
                f"\tpkgdesc = {v['pkgdesc'][0]}",
                f"\tpkgver = {v['pkgver'][0]}",
                f"\tpkgrel = {v['pkgrel'][0]}",
                f"\turl = {v['url'][0]}",
                *[f"\tarch = {item}" for item in v["arch"]],
                *[f"\tlicense = {item}" for item in v["license"]],
                *[f"\tdepends = {item}" for item in v["depends"]],
                *[f"\tsource = {item}" for item in v["source"]],
                *[f"\tsha256sums = {item}" for item in v["sha256sums"]],
                "",
                "pkgname = venus-mouse"]
    assert (ARCH_DIR / ".SRCINFO").read_text(encoding="utf-8").splitlines() == expected


def test_package_function_lays_out_the_shared_payload(tmp_path):
    """Run package() as makepkg would, on a GitHub-style release tarball of the tracked files."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    version = _project_version()
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=PROJECT, capture_output=True,
                             check=True).stdout.decode().split("\0")
    if "packaging/arch/PKGBUILD" not in tracked or "packaging/install_tree.py" not in tracked:
        pytest.skip("packaging files are not tracked by git yet")
    srcdir, pkgdir = tmp_path / "src", tmp_path / "pkg"
    srcdir.mkdir()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in filter(None, tracked):
            if (PROJECT / name).is_file():
                tar.add(PROJECT / name, arcname=f"Venus-Mouse-Linux-{version}/{name}")
    raw.seek(0)
    with tarfile.open(fileobj=raw) as tar:
        tar.extractall(srcdir, filter="data")
    result = subprocess.run(
        ["bash", "-ec", f'source "{PKGBUILD}"; cd "$srcdir"; package'],
        env={"PATH": "/usr/bin:/bin", "srcdir": str(srcdir), "pkgdir": str(pkgdir),
             "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr

    spec = importlib.util.spec_from_file_location("install_tree", PROJECT / "packaging" / "install_tree.py")
    tree = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tree)
    payload = tree.install(tmp_path / "expected")
    extras = ["usr/share/doc/venus-mouse/README.md", "usr/share/licenses/venus-mouse/AlexBrush-OFL.txt",
              "usr/share/licenses/venus-mouse/LICENSE"]
    built = sorted(str(p.relative_to(pkgdir)) for p in pkgdir.rglob("*") if not p.is_dir())
    assert built == sorted(payload + extras)
    for name in payload:
        assert (pkgdir / name).read_bytes() == (tmp_path / "expected" / name).read_bytes(), name
    assert (pkgdir / "usr/share/licenses/venus-mouse/LICENSE").read_bytes() == (PROJECT / "LICENSE").read_bytes()
    assert not list(pkgdir.rglob("*.py[co]")) and not list(pkgdir.rglob("__pycache__"))


def _pkg_builder():
    spec = importlib.util.spec_from_file_location("arch_build_pkg", ARCH_DIR / "build_pkg.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ready_made_package_builds_in_a_fixed_neutral_folder():
    builder = _pkg_builder()
    assert builder.package_name() == f"venus-mouse-{_project_version()}-1-any.pkg.tar.zst"
    assert str(builder.TOPDIR) == "/tmp/venus-mouse-archbuild"      # recorded in .BUILDINFO
    assert builder.PACKAGER == "Berry Dingle <dzll-launcher@tutamail.com>"
    text = (ARCH_DIR / "build_pkg.py").read_text(encoding="utf-8")
    assert '["makepkg", "-d", "--cleanbuild", "--noconfirm"]' in text
    for forbidden in ("sudo", "pacman -U", "-si", "--install", "/dev/"):
        assert forbidden not in text.split('"""', 2)[2], forbidden    # code, not the docstring


@pytest.mark.skipif(shutil.which("makepkg") is None, reason="makepkg not installed (Arch only)")
def test_ready_made_package_is_reproducible_and_has_no_build_paths(tmp_path):
    builder = _pkg_builder()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = builder.build(tmp_path / "a"), builder.build(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(subprocess.run(["zstd", "-dc", str(first)], capture_output=True,
                                                         check=True).stdout)) as tar:
        info = tar.extractfile(".BUILDINFO").read().decode()
        assert "builddir = /tmp/venus-mouse-archbuild" in info and "/home/" not in info
        assert all(m.uname == "root" and m.gname == "root" for m in tar.getmembers())
