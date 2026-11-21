# Fedora rpm

One **noarch** package, `venus-mouse-<version>-1.noarch.rpm`, for Fedora 44,
45 and later. Fedora 45 moves from Python 3.14 to 3.15. That would need a
separate build per release only for packages installed into Python's own
`site-packages` (rpm then requires that exact `python(abi)`). This package
installs the app as an application's private modules in `/usr/share/venus-mouse`
and runs it with `/usr/bin/python3`, so it requires only `python3 >= 3.11`,
`python3-gobject`, `gtk4 >= 4.10`, `libadwaita >= 1.5` and
`hicolor-icon-theme`. No `.pyc` files are shipped (the spec's `%check` fails
the build if any appear). There is therefore no dist tag in the file name.

## Build

```bash
python3 packaging/rpm/build_rpm.py
# -> build/rpm/venus-mouse-<version>-1.noarch.rpm (+ the .src.rpm)
```

Needs `rpm-build`, `desktop-file-utils` and `systemd-rpm-macros`. The source
tarball is made from the files git tracks (their current contents), so new
files must be `git add`ed first. Timestamps come from the last commit: the
same tree builds a byte-identical rpm. The build never installs anything,
never runs the app and never touches a device.

## What it installs

| Path | What |
|---|---|
| `/usr/share/venus-mouse/` | the app (laid out by the shared `packaging/install_tree.py`, also used by the deb and Arch packages): exactly the AppImage's files (`build_appimage.bundle_files()`: the import closure of the GUI, its three device helpers and the click counter, plus their resources) and the app's own icon folder |
| `/usr/bin/venus-mouse` | launcher (`--click-counter` opens the click counter) |
| `/usr/share/applications/io.github.berry_dingle.VenusMouse.desktop` | desktop entry (`Exec=venus-mouse`) |
| `/usr/share/icons/hicolor/*/apps/` | icons |
| `/usr/lib/udev/rules.d/70-utechsmart-venus.rules` | the project's udev rule: the logged-in desktop user gets access to the mouse's configuration interface (`uaccess`), plus mode 0660 for the `venus-mouse` group |
| `/usr/lib/sysusers.d/venus-mouse.conf` | creates the `venus-mouse` system group (rpm does this itself; no scripts) |
| `/usr/share/licenses/venus-mouse/` | GPL-3.0 `LICENSE`, Alex Brush OFL-1.1 |

The package has **no install scripts**: it adds no users to the group and
does not trigger udev. After installing, **unplug and replug the mouse** once
so the rule applies. Group membership stays an administrator's choice and is
not needed on a desktop.

## Install / remove (user-run)

```bash
sudo dnf install ./build/rpm/venus-mouse-0.2.2-1.noarch.rpm
sudo dnf remove venus-mouse
```

Checked offline: `rpmlint` (0 errors; warnings: no URL tag yet, no man page,
font licence also kept beside the font), and every requirement resolves
against the Fedora 45 repositories (Python 3.15.2, PyGObject 3.58, GTK 4.24,
libadwaita 1.10). Not yet run on Python 3.15.
