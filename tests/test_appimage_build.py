"""AppImage packaging: only runtime files go in, and the bundle is complete (offline; no build of the image)."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "packaging" / "appimage" / "build_appimage.py"


def _builder():
    spec = importlib.util.spec_from_file_location("build_appimage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundle_has_the_entry_points_and_resources_but_no_development_files():
    files = _builder().bundle_files()
    for needed in ("venus_profile/gui/application.py", "venus_profile/gui/__main__.py",
                   "venus_profile/read_snapshot.py", "venus_profile/watch_events.py",
                   "venus_profile/preview_lighting.py", "venus_profile/gui/click_counter.py",
                   "venus_profile/gui/styles.css", "venus_profile/data/read_plan/factory_read_plan.json",
                   "venus_profile/data/factory/RSProfile5.pfd", "venus_profile/gui/data/fonts/AlexBrush-OFL.txt"):
        assert needed in files, needed
    for unwanted in ("guarded_", "_analysis", "read_probe", "snapshot_audit", "gui/safety.py",
                     "gui/data/macros", "venus_mouse_icon", "venus_side_view_crop", "__pycache__", "verify_udev_access",
                     "read_device_memory", "visual_colour_cycle", "device_diagnostics"):
        assert not [f for f in files if unwanted in f], unwanted
    assert all((PROJECT / "src" / f).is_file() for f in files)


def test_built_appdir_runs_the_app_and_helpers_on_its_own(tmp_path, monkeypatch):
    builder = _builder()
    appdir = tmp_path / "AppDir"
    builder.build_appdir(appdir)
    lib = appdir / builder.LIB_DIR
    assert (appdir / "AppRun").stat().st_mode & 0o111 and os.readlink(appdir / ".DirIcon").endswith(".png")
    assert not list(appdir.rglob("__pycache__")) and not list(appdir.rglob("*.pyc"))
    licence = appdir / "usr/share/licenses/venus-mouse/LICENSE"
    assert licence.read_bytes() == (PROJECT / "LICENSE").read_bytes()          # GPL-3.0 travels with it
    code = (
        "import importlib, sys\n"
        "for name in ('venus_profile.gui.application', 'venus_profile.read_snapshot', "
        "'venus_profile.watch_events', 'venus_profile.preview_lighting', 'venus_profile.gui.click_counter', "
        "'venus_profile.gui.buttons.page', 'venus_profile.gui.lighting.page', 'venus_profile.gui.macros.page', "
        "'venus_profile.gui.dpi.page', 'venus_profile.gui.settings'):\n"
        "    module = importlib.import_module(name)\n"
        f"    assert module.__file__.startswith({str(lib)!r}), module.__file__\n"
        "from venus_profile.app_store import factory_profile_bytes\n"
        "from venus_profile.read_snapshot import packaged_read_plan_path\n"
        "assert len(factory_profile_bytes(3)) > 1000 and packaged_read_plan_path().is_file()\n"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(tmp_path), "PYTHONPATH": str(lib),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr


def test_apprun_is_posix_shell_and_desktop_entry_gets_the_version(tmp_path):
    builder = _builder()
    apprun = SCRIPT.parent / "AppRun"
    assert subprocess.run(["sh", "-n", str(apprun)]).returncode == 0
    text = apprun.read_text(encoding="utf-8")
    assert 'exec "$PYTHON" -m venus_profile.gui' in text and "--click-counter" in text
    assert "sudo" not in text.split("missing()")[0]                    # nothing is run with sudo
    builder.build_appdir(tmp_path / "AppDir")
    desktop = (tmp_path / "AppDir" / f"{builder.APP_ID}.desktop").read_text(encoding="utf-8")
    assert f"X-AppImage-Version={builder.version()}" in desktop
    assert f"Icon={builder.APP_ID}" in desktop and "Exec=" in desktop and "Categories=Utility;\n" in desktop


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash not installed")
def test_apprun_reports_missing_dependencies(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "python3").write_text('#!/bin/sh\necho "Namespace Adw not available" >&2\nexit 1\n')
    (fake / "python3").chmod(0o755)
    result = subprocess.run(["dash", str(SCRIPT.parent / "AppRun")], capture_output=True, text=True,
                            env={"PATH": f"{fake}:/usr/bin", "HOME": str(tmp_path)}, timeout=30)
    assert result.returncode == 1
    assert "needs these installed" in result.stderr and "Namespace Adw not available" in result.stderr


def test_runtime_magic_is_checked(tmp_path):
    bogus = tmp_path / "runtime"
    bogus.write_bytes(b"\x7fELF" + bytes(20))
    with pytest.raises(SystemExit):
        _builder().read_runtime(bogus)
