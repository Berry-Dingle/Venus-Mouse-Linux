# Debian / Ubuntu deb

One **`Architecture: all`** package, `venus-mouse_<version>-1_all.deb`, for
**Debian 13+** and **Ubuntu 24.04+**. Same payload as the rpm
(`packaging/install_tree.py`: the app in `/usr/share/venus-mouse`, run by
`/usr/bin/python3`), so it does not depend on a particular Python version.

Depends: `python3 (>= 3.11)`, `python3-gi`, `gir1.2-gtk-4.0 (>= 4.10)`,
`gir1.2-adw-1 (>= 1.5)`, `hicolor-icon-theme`. Checked against the official
package lists (2026-11-20): Ubuntu 24.04 (GTK 4.14, libadwaita 1.5, Python
3.12) and Debian 13 (GTK 4.18, libadwaita 1.7, Python 3.13) satisfy them;
Debian 12 does not (GTK 4.8, libadwaita 1.2), so apt refuses to install it
there, matching the AppImage's requirements.

## Build

```bash
python3 packaging/deb/build_deb.py
# -> build/deb/venus-mouse_<version>-1_all.deb
```

Needs `dpkg-deb` (on Fedora: the `dpkg` package). Built straight from the
staged tree with `dpkg-deb --root-owner-group`, no debhelper. Timestamps come
from the last commit: the same tree builds a byte-identical package.

## What it adds to the shared payload

- `DEBIAN/control`, `DEBIAN/md5sums`.
- `DEBIAN/postinst` (on `configure` only): creates the optional `venus-mouse`
  group with `systemd-sysusers venus-mouse.conf` (or `addgroup --system`) and
  runs `udevadm control --reload-rules`. It adds nobody to the group and
  triggers no device: **replug the mouse once** after installing.
- `/usr/share/doc/venus-mouse/`: `copyright` (DEP-5; GPL-3.0+, and the Alex
  Brush font's OFL-1.1 text), `changelog.Debian.gz`, `README.md`.

Removing the package removes its files; the group is kept (as the udev
docs recommend).

## Install / remove (user-run)

```bash
sudo apt install ./build/deb/venus-mouse_0.2.0-1_all.deb
sudo apt remove venus-mouse
```

Not yet installed on a real Debian or Ubuntu system.
