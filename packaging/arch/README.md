# Arch Linux (AUR)

`PKGBUILD` and `.SRCINFO` for an **`arch=('any')`** package. Same payload as
the rpm and deb (`packaging/install_tree.py`: the app in
`/usr/share/venus-mouse`, run by the system Python), so Arch's frequent
Python updates need no rebuild.

Depends: `python>=3.11`, `python-gobject`, `gtk4>=4.10`, `libadwaita>=1.5`,
`hicolor-icon-theme` (all in Arch's core/extra, checked 2026-11-20). No
install scriptlet: pacman's systemd hooks create the `venus-mouse` group from
`/usr/lib/sysusers.d` and reload the udev rules. **Replug the mouse once**
after installing.

## Source

The PKGBUILD downloads the GitHub release tarball for tag `v<pkgver>`
(`…/archive/refs/tags/v0.2.0.tar.gz`) and checks its SHA-256. For each new
release: push the tag, bump `pkgver` (reset `pkgrel=1`), run `updpkgsums`,
and regenerate `.SRCINFO` (`makepkg --printsrcinfo > .SRCINFO`).

## Ready-made package (for the release page)

```bash
python3 packaging/arch/build_pkg.py          # on Arch, or in an Arch container:
distrobox enter <arch-box> -- python3 packaging/arch/build_pkg.py
```

Runs `makepkg` on the PKGBUILD (so the `v<pkgver>` tag must be pushed first)
and writes `build/arch/venus-mouse-<pkgver>-<pkgrel>-any.pkg.tar.zst`, which
users install with `sudo pacman -U`. Built in the fixed folder
`/tmp/venus-mouse-archbuild` with `SOURCE_DATE_EPOCH` = the tag's commit time,
so `.BUILDINFO` records no user name or checkout path and every build is
byte-identical. `-d` skips makepkg's installed-dependencies check (nothing is
compiled, so the build doesn't need them).

## Build and install yourself (on Arch)

```bash
cd packaging/arch
makepkg -si
```

Checked: v0.2.0 built with makepkg 7.1.0 in an Arch container (2026-11-20),
twice, byte-identical; same payload as the deb, plus the licences in
`/usr/share/licenses/venus-mouse/`, all files root-owned. Not yet installed
and run on a real Arch system.
