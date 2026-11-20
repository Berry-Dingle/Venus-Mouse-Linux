# Venus hidraw permissions

The Venus F2 read protocol sends a validated feature SET_REPORT before each
GET_REPORT. Linux therefore requires the hidraw node to be opened with
`O_RDWR`, even though the operation only reads device memory.

## Scope and security

The packaged rule grants mode `0660` to the dedicated `venus-mouse` group.
It does not use `0666`, does not affect all hidraw devices, and does not grant
configuration access to the mouse's normal interfaces 00 or 01.

`bInterfaceNumber` is stored on USB interface `1-6:1.2`; `idVendor` and
`idProduct` are stored one level higher on USB device `1-6`. Udev requires
multiple parent-matching keys in one rule to match the same parent. The rule
therefore imports the USB-device identity through the standard `usb_id`
built-in and separately requires the interface ancestor to expose
`bInterfaceNumber=="02"`. It is independent of USB port and hidraw number.

## Preview and install

Preview only:

```bash
cd linux-suite-prototype   # the repository root
tools/install_udev_rule.sh
```

Explicit installation:

```bash
sudo tools/install_udev_rule.sh --install
```

If an installed file with the same name differs, installation stops. Review
the difference and use `--install --replace` only when replacement is intended.

The installer creates the system group only if absent, uses `SUDO_USER` for
group membership, installs the rule as root-owned mode 0644, and reloads the
udev rules. It does not open the mouse or issue a broad trigger.

After installation:

1. Log out and back in, or start a new login session, if `venus-mouse` is not
   yet visible in `id -nG`.
2. Unplug and reconnect the mouse so the new rule processes its device event.
3. Verify metadata and effective access without opening hidraw:

```bash
PYTHONPATH=src python3 -m venus_profile.verify_udev_access
```

The verifier dynamically selects `04d9:fc55` interface 02 and, when practical,
usage page `0xFFA0`, usage 1. It prints owner, group, mode, and current-user
read/write access. It uses sysfs enumeration, `stat`, and `access` only.

## Uninstall

Preview:

```bash
tools/uninstall_udev_rule.sh
```

Explicit removal:

```bash
sudo tools/uninstall_udev_rule.sh --uninstall
```

Removal is refused if the installed file differs from the packaged project
rule. The helper removes neither the group nor user membership, avoiding
destructive account changes. Unplug/replug afterward.

## RPM/DEB packaging

All three packages install the rule in `/usr/lib/udev/rules.d/` and the group
file `/usr/lib/sysusers.d/venus-mouse.conf`. The Fedora rpm (`packaging/rpm/`)
and Arch package (`packaging/arch/`) have no maintainer scripts: rpm and
pacman's systemd hooks create the group and reload the rules. The deb
(`packaging/deb/`) has a `postinst` that runs `systemd-sysusers` (or
`addgroup --system`) and `udevadm control --reload-rules`, nothing else. Other
packages should install the rule into the distribution's udev rules
directory as mode 0644 and create `venus-mouse` through the package's standard
system-group mechanism. Package maintainer scripts may reload udev rules but
should not broadly trigger hidraw devices, edit user memberships, or assume a
desktop user. Group membership should remain an explicit administrator choice.

On package removal, remove only the package-owned rule. Retaining the system
group is normally safer than deleting a group that administrators may still
use; distribution policy should decide final purge behaviour.
