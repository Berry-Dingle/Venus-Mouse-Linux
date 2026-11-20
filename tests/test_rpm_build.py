"""The rpm: one noarch package, app in a private directory, same app files as the AppImage."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PROJECT = Path(__file__).parents[1]
RPM_DIR = PROJECT / "packaging" / "rpm"
SPEC = RPM_DIR / "venus-mouse.spec"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree():
    return _load("install_tree", PROJECT / "packaging" / "install_tree.py")


def _builder():
    return _load("rpm_build", RPM_DIR / "build_rpm.py")


def test_payload_has_exactly_the_appimage_app_files_and_no_bytecode(tmp_path):
    tree = _tree()
    files = tree.install(tmp_path)
    app = [f[len("usr/share/venus-mouse/"):] for f in files if f.startswith("usr/share/venus-mouse/")]
    bundle = tree.build_appimage.bundle_files()
    assert set(bundle) <= set(app)
    assert {f for f in app if not f.startswith("venus_profile/gui/data/icons/")} == set(bundle)
    assert not list(tmp_path.rglob("__pycache__")) and not list(tmp_path.rglob("*.py[co]"))
    assert not [f for f in files if "/python3" in f or "site-packages" in f]
    for needed in ("usr/bin/venus-mouse", "usr/lib/udev/rules.d/70-utechsmart-venus.rules",
                   "usr/lib/sysusers.d/venus-mouse.conf",
                   "usr/share/applications/io.github.berry_dingle.VenusMouse.desktop",
                   "usr/share/icons/hicolor/256x256/apps/io.github.berry_dingle.VenusMouse.png",
                   "usr/share/icons/hicolor/scalable/apps/io.github.berry_dingle.VenusMouse.svg"):
        assert needed in files, needed
    assert (tmp_path / "usr/bin/venus-mouse").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "usr/share/venus-mouse/venus_profile/gui/application.py").stat().st_mode & 0o777 == 0o644


def test_installed_tree_runs_the_app_and_helpers_from_the_private_directory(tmp_path):
    _tree().install(tmp_path / "root")
    lib = tmp_path / "root" / "usr/share/venus-mouse"
    code = (
        "import importlib\n"
        "for name in ('venus_profile.gui.application', 'venus_profile.read_snapshot', "
        "'venus_profile.watch_events', 'venus_profile.preview_lighting', 'venus_profile.gui.click_counter', "
        "'venus_profile.gui.buttons.page', 'venus_profile.gui.lighting.page', 'venus_profile.gui.macros.page', "
        "'venus_profile.gui.dpi.page', 'venus_profile.gui.settings'):\n"
        "    module = importlib.import_module(name)\n"
        f"    assert module.__file__.startswith({str(lib)!r}), module.__file__\n"
        "from venus_profile.app_store import factory_profile_bytes\n"
        "from venus_profile.read_snapshot import packaged_read_plan_path\n"
        "from venus_profile.gui.assets import artwork_bytes, icon_bytes, TOP_ARTWORK\n"
        "assert len(factory_profile_bytes(3)) > 1000 and packaged_read_plan_path().is_file()\n"
        "assert artwork_bytes(TOP_ARTWORK) and icon_bytes()\n"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(tmp_path), "PYTHONPATH": str(lib),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not list(lib.rglob("__pycache__"))


def test_launcher_is_posix_shell_on_system_python_without_privilege():
    launcher = PROJECT / "packaging" / "venus-mouse"
    assert subprocess.run(["sh", "-n", str(launcher)]).returncode == 0
    text = launcher.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert 'PYTHONPATH="/usr/share/venus-mouse' in text and "PYTHONDONTWRITEBYTECODE=1" in text
    assert "exec /usr/bin/python3 -m venus_profile.gui \"$@\"" in text
    assert "exec /usr/bin/python3 -m venus_profile.gui.click_counter" in text
    for forbidden in ("sudo", "pkexec", "udevadm", "/dev/", "hidraw"):
        assert forbidden not in text


def test_desktop_entry_launches_the_packaged_command(tmp_path):
    tree = _tree()
    tree.install(tmp_path)
    desktop = tmp_path / "usr/share/applications/io.github.berry_dingle.VenusMouse.desktop"
    text = desktop.read_text(encoding="utf-8")
    assert "Exec=venus-mouse\n" in text and "venus-profile-gui" not in text
    assert "Icon=io.github.berry_dingle.VenusMouse\n" in text
    assert "Categories=Utility;\n" in text          # the Utilities menu (user, 2026-11-20)
    if shutil.which("desktop-file-validate"):
        result = subprocess.run(["desktop-file-validate", str(desktop)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_udev_rule_and_group_match_the_project_rule(tmp_path):
    _tree().install(tmp_path)
    installed = (tmp_path / "usr/lib/udev/rules.d/70-utechsmart-venus.rules").read_bytes()
    assert installed == (PROJECT / "packaging/udev/70-utechsmart-venus.rules").read_bytes()
    sysusers = (tmp_path / "usr/lib/sysusers.d/venus-mouse.conf").read_text(encoding="utf-8")
    entries = [line.split() for line in sysusers.splitlines() if line and not line.startswith("#")]
    assert entries == [["g", "venus-mouse", "-"]]          # a group only: no users, no memberships
    assert 'GROUP="venus-mouse"' in installed.decode()


def test_spec_is_one_noarch_package_independent_of_the_python_version():
    text = SPEC.read_text(encoding="utf-8")
    assert "BuildArch:      noarch" in text
    assert _builder().spec_version() == _builder().project_version()
    for required in ("Requires:       python3 >= 3.11", "Requires:       python3-gobject",
                     "Requires:       gtk4 >= 4.10", "Requires:       libadwaita >= 1.5"):
        assert required in text, required
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(lines)
    for forbidden in ("python3_sitelib", "python3_version", "python(abi)", "%pyproject", "%py_byte_compile",
                      "groupadd", "useradd", "usermod", "udevadm", "sudo"):
        assert forbidden not in body, forbidden
    sections = {line.split()[0] for line in lines if line.startswith("%") and line.split()}
    assert not sections & {"%pre", "%post", "%preun", "%postun", "%pretrans", "%posttrans", "%triggerin"}
    assert "License:        GPL-3.0-or-later AND OFL-1.1" in text
    assert "%license LICENSE" in text and "%license src/venus_profile/gui/data/fonts/AlexBrush-OFL.txt" in text


def test_source_tarball_is_deterministic_and_holds_the_packaging(tmp_path):
    import tarfile

    builder = _builder()
    first = builder.make_tarball(tmp_path / "a.tar.gz", "0.2.0", 1_700_000_000)
    second = builder.make_tarball(tmp_path / "b.tar.gz", "0.2.0", 1_700_000_000)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as tar:
        names = tar.getnames()
        assert all(name.startswith("venus-mouse-0.2.0/") for name in names)
        assert {member.mtime for member in tar.getmembers()} == {1_700_000_000}
    for needed in ("packaging/install_tree.py", "packaging/venus-mouse",
                   "packaging/appimage/build_appimage.py", "LICENSE", "README.md"):
        if needed in builder.tracked_files():
            assert f"venus-mouse-0.2.0/{needed}" in names


@pytest.mark.skipif(shutil.which("rpmbuild") is None or shutil.which("git") is None,
                    reason="rpmbuild or git not installed")
def test_built_rpm_has_no_python_abi_lock_bytecode_or_scripts(tmp_path):
    builder = _builder()
    tracked = builder.tracked_files()
    if not all(name in tracked for name in ("packaging/rpm/venus-mouse.spec", "packaging/install_tree.py",
                                            "packaging/venus-mouse", "packaging/venus-mouse.sysusers")):
        pytest.skip("rpm packaging files are not tracked by git yet")
    rpm = builder.build(tmp_path)
    assert rpm.name == f"venus-mouse-{builder.spec_version()}-1.noarch.rpm"

    def query(*args):
        return subprocess.run(["rpm", "-qp", *args, str(rpm)], capture_output=True, text=True,
                              check=True).stdout

    requires = query("--requires")
    assert "python(abi)" not in requires and "python3 >= 3.11" in requires
    files = query("--list").splitlines()
    assert not [f for f in files if f.endswith((".pyc", ".pyo")) or "__pycache__" in f]
    assert not [f for f in files if "site-packages" in f]
    assert "/usr/share/venus-mouse/venus_profile/gui/application.py" in files
    assert "/usr/lib/udev/rules.d/70-utechsmart-venus.rules" in files
    assert query("--scripts").strip() == ""                 # no maintainer scripts at all
    assert "group(venus-mouse)" in query("--provides")      # rpm creates the group from sysusers.d

    # Reproducible from any checkout: the spec inside the .src.rpm carries the commit time,
    # not the file's on-disk time.
    srpm = tmp_path / f"venus-mouse-{builder.spec_version()}-1.src.rpm"
    dump = subprocess.run(["rpm", "-qp", "--dump", str(srpm)], capture_output=True, text=True,
                          check=True).stdout.split("\n")
    spec_line = next(line for line in dump if line.startswith("venus-mouse.spec "))
    assert int(spec_line.split()[2]) == builder.source_epoch()
    # No checkout path or user name inside either package (rpmbuild records its build paths).
    for package in (rpm, srpm):
        data = package.read_bytes()
        assert str(PROJECT).encode() not in data and str(tmp_path).encode() not in data
        assert b"/home/" not in data
