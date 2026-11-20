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

## Build and install (user-run, on Arch)

```bash
cd packaging/arch
makepkg -si
```

Checked here without makepkg (not installed on the Fedora build machine):
`bash -n`, `.SRCINFO` matches the PKGBUILD, and `package()` run on a
GitHub-style tarball of the tracked files gives exactly the shared payload
plus licences and README. Not yet built with makepkg or installed on Arch.
