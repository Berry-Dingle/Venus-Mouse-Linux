# AppImage

A *thin* AppImage: it carries the app's own code and data (about 3.0 MB) and
uses the system's Python 3.11+, GTK 4.10+, libadwaita 1.5+ and PyGObject.
Those ship with current GNOME desktops and most Fedora, Ubuntu (24.04+),
Debian (13+) and Arch installs. If any are missing, `AppRun` says what to
install (terminal, plus zenity/kdialog/notify-send when available).

## Build

```bash
# once: the official AppImage type-2 runtime (MIT), kept out of git
mkdir -p build/tools
curl -fL -o build/tools/runtime-x86_64 \
  https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64

python3 packaging/appimage/build_appimage.py
# -> build/appimage/Venus_Mouse-<version>-x86_64.AppImage (+ .sha256)
```

Needs `mksquashfs` (squashfs-tools). The build never runs the app or touches a
device. Build used for 0.2.0: runtime SHA-256
`617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46`.

## What goes in

- The Python modules reachable by import from the GUI and the three device
  helpers it launches (`read_snapshot`, `watch_events`, `preview_lighting`),
  plus the click counter: worked out from the imports at build time.
- The resources those modules load (`RESOURCE_FILES` in the build script):
  stylesheet, three images, the title font **with its OFL licence**, the app
  icons, the five factory profiles and the read plan.
- `AppRun`, the desktop entry, the icon and the project's `LICENSE` (GPL-3.0).

Left out: tests, docs, the investigation, the guarded CLI writers, the capture
analysis scripts, the read probe and audits, the sample macros used by the
tests, an unused icon, and every `__pycache__`.
`tests/test_appimage_build.py` checks both lists and that a freshly built
AppDir can import the app and helpers on its own.

## Use

```bash
chmod +x Venus_Mouse-0.2.0-x86_64.AppImage
./Venus_Mouse-0.2.0-x86_64.AppImage                  # the app
./Venus_Mouse-0.2.0-x86_64.AppImage --click-counter  # fire-key click counter
```

Mouse access: AppImage users run the copy-paste command from
*Settings → Set up mouse access…* once (needs a systemd desktop), then replug
the mouse. Without FUSE, run with `APPIMAGE_EXTRACT_AND_RUN=1` or
`--appimage-extract`.
