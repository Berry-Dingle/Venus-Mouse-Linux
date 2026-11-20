"""Build the Venus Mouse AppImage (thin: uses the host's Python, GTK 4, libadwaita, PyGObject).

    python3 packaging/appimage/build_appimage.py [--runtime PATH] [--output DIR]

Only what the application needs goes in:

* the Python modules reachable from the GUI entry point and the three device
  helpers it launches (``read_snapshot``, ``watch_events``, ``preview_lighting``),
  plus the click-counter diagnostic, found by following their imports, with
  every package ``__init__`` on the way;
* the resource files those modules load (``RESOURCE_FILES``);
* ``AppRun`` (dependency check + launch), the desktop entry, the icon and the
  project's GPL-3.0 ``LICENSE``.

Nothing from ``tests/``, ``docs/``, the investigation, the guarded CLI writers or
the capture-analysis scripts is copied. The build never runs the app and never
touches a device. It needs ``mksquashfs`` (squashfs-tools) and the official
AppImage type-2 runtime (``build/tools/runtime-x86_64`` by default; it is
checked for the ``AI\\x02`` magic and its SHA-256 is printed).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
SRC = PROJECT / "src"
PACKAGING = PROJECT / "packaging"
APP_NAME = "Venus_Mouse"
APP_ID = "io.github.berry_dingle.VenusMouse"
LIB_DIR = Path("usr/lib/venus-mouse")

#: Modules the AppImage can start (GUI, the helpers it launches, diagnostics).
ENTRY_MODULES = (
    "venus_profile.gui.__main__",
    "venus_profile.read_snapshot",
    "venus_profile.watch_events",
    "venus_profile.preview_lighting",
    "venus_profile.gui.click_counter",
)

#: Non-Python files the bundled modules load at run time (relative to src/).
RESOURCE_FILES = (
    "venus_profile/gui/styles.css",
    "venus_profile/gui/data/__init__.py",
    "venus_profile/gui/data/venus_neon_icon.png",
    "venus_profile/gui/data/venus_top_view_crop.png",
    "venus_profile/gui/data/fonts/AlexBrush-Regular.ttf",
    "venus_profile/gui/data/fonts/AlexBrush-OFL.txt",        # the font's licence travels with it
    "venus_profile/data/__init__.py",
    "venus_profile/data/factory/__init__.py",
    "venus_profile/data/factory/RSProfile1.pfd",
    "venus_profile/data/factory/RSProfile2.pfd",
    "venus_profile/data/factory/RSProfile3.pfd",
    "venus_profile/data/factory/RSProfile4.pfd",
    "venus_profile/data/factory/RSProfile5.pfd",
    "venus_profile/data/read_plan/factory_read_plan.json",
)
ICON_SIZES = ("16x16", "24x24", "32x32", "48x48", "64x64", "128x128", "256x256", "512x512")

RUNTIME_MAGIC = b"AI\x02"


def _module_file(name: str) -> Path | None:
    path = SRC.joinpath(*name.split("."))
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    return None


def module_closure(entries=ENTRY_MODULES) -> set[str]:
    """Every venus_profile module reachable by import from the entries, plus parent packages."""
    seen: set[str] = set()
    todo = list(entries)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = _module_file(name)
        if path is None:
            continue                         # e.g. "pkg.function" from "from pkg import function"
        seen.add(name)
        parts = name.split(".")
        todo.extend(".".join(parts[:i]) for i in range(1, len(parts)))      # parent packages
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                todo.extend(alias.name for alias in node.names if alias.name.startswith("venus_profile"))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")[: len(package.split(".")) - node.level + 1]
                    module = ".".join(base + ([node.module] if node.module else []))
                elif node.module and node.module.startswith("venus_profile"):
                    module = node.module
                else:
                    continue
                todo.append(module)
                todo.extend(f"{module}.{alias.name}" for alias in node.names)
    return seen


def bundle_files() -> list[str]:
    """Paths (relative to src/) copied into the AppImage, sorted."""
    files = {str(_module_file(name).relative_to(SRC)) for name in module_closure()}
    files.update(RESOURCE_FILES)
    return sorted(files)


def read_runtime(path: Path) -> bytes:
    data = path.read_bytes()
    if data[8:11] != RUNTIME_MAGIC or data[:4] != b"\x7fELF":
        raise SystemExit(f"{path} is not an AppImage type-2 runtime (no ELF / AI\\x02 magic)")
    return data


def version() -> str:
    for line in (PROJECT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def build_appdir(appdir: Path) -> list[str]:
    if appdir.exists():
        shutil.rmtree(appdir)
    lib = appdir / LIB_DIR
    copied = []
    for relative in bundle_files():
        source = SRC / relative
        if not source.is_file():
            raise SystemExit(f"missing file for the bundle: {source}")
        target = lib / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    icons = SRC / "venus_profile/gui/data/icons/hicolor"
    for size in ICON_SIZES:
        source = icons / size / "apps" / f"{APP_ID}.png"
        target = lib / "venus_profile/gui/data/icons/hicolor" / size / "apps" / f"{APP_ID}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target.relative_to(lib)))
    scalable = icons / "scalable" / "apps" / f"{APP_ID}.svg"
    target = lib / "venus_profile/gui/data/icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(scalable, target)
    copied.append(str(target.relative_to(lib)))

    licence = appdir / "usr/share/licenses/venus-mouse/LICENSE"     # GPL-3.0: the licence travels with the binary
    licence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / "LICENSE", licence)
    copied.append(str(licence.relative_to(appdir)))

    here = Path(__file__).parent
    shutil.copy2(here / "AppRun", appdir / "AppRun")
    os.chmod(appdir / "AppRun", 0o755)
    desktop = (here / f"{APP_ID}.desktop").read_text(encoding="utf-8")
    desktop = "\n".join(f"X-AppImage-Version={version()}" if line.startswith("X-AppImage-Version=") else line
                         for line in desktop.splitlines()) + "\n"
    (appdir / f"{APP_ID}.desktop").write_text(desktop, encoding="utf-8")
    shutil.copy2(icons / "256x256" / "apps" / f"{APP_ID}.png", appdir / f"{APP_ID}.png")
    os.symlink(f"{APP_ID}.png", appdir / ".DirIcon")
    for path in appdir.rglob("*"):
        if path.is_symlink():
            continue
        os.chmod(path, 0o755 if path.is_dir() or path.name == "AppRun" else 0o644)
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", type=Path, default=PROJECT / "build/tools/runtime-x86_64")
    parser.add_argument("--output", type=Path, default=PROJECT / "build/appimage")
    args = parser.parse_args(argv)
    if shutil.which("mksquashfs") is None:
        raise SystemExit("mksquashfs not found (install squashfs-tools)")
    runtime = read_runtime(args.runtime)
    args.output.mkdir(parents=True, exist_ok=True)
    appdir = args.output / "AppDir"
    copied = build_appdir(appdir)
    squashfs = args.output / "image.squashfs"
    squashfs.unlink(missing_ok=True)
    subprocess.run(["mksquashfs", str(appdir), str(squashfs), "-root-owned", "-noappend", "-no-xattrs",
                    "-comp", "zstd", "-all-time", "0", "-mkfs-time", "0", "-quiet"], check=True)
    image = args.output / f"{APP_NAME}-{version()}-x86_64.AppImage"
    image.write_bytes(runtime + squashfs.read_bytes())
    os.chmod(image, 0o755)
    squashfs.unlink()
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (image.parent / f"{image.name}.sha256").write_text(f"{digest}  {image.name}\n", encoding="utf-8")
    print(f"runtime  sha256 {hashlib.sha256(runtime).hexdigest()}  ({args.runtime})")
    print(f"bundled  {len(copied)} files from src/ + AppRun, desktop entry, icon")
    print(f"built    {image}  ({image.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256   {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
