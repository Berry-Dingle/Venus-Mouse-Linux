from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
import subprocess

from venus_profile.readonly_hidraw import HidrawDevice
from venus_profile.verify_udev_access import describe_permissions


PROJECT = Path(__file__).parents[1]
RULE = PROJECT / "packaging" / "udev" / "70-utechsmart-venus.rules"
INSTALLER = PROJECT / "tools" / "install_udev_rule.sh"
UNINSTALLER = PROJECT / "tools" / "uninstall_udev_rule.sh"
VERIFIER = PROJECT / "src" / "venus_profile" / "verify_udev_access.py"


def _rule_text() -> str:
    return RULE.read_text(encoding="utf-8")


def test_rule_has_exact_device_and_interface_scope():
    rule = _rule_text()
    assert 'ENV{ID_VENDOR_ID}=="04d9"' in rule
    assert 'ENV{ID_MODEL_ID}=="fc55"' in rule
    assert 'ATTRS{bInterfaceNumber}=="02"' in rule
    assert 'SUBSYSTEM!="hidraw"' in rule
    assert 'IMPORT{builtin}="usb_id"' in rule


def test_rule_grants_only_group_mode_0660():
    rule = _rule_text()
    assert 'GROUP="venus-mouse"' in rule
    assert 'MODE="0660"' in rule
    assert "0666" not in rule
    assert 'SUBSYSTEM=="hidraw", MODE=' not in rule


def test_interfaces_00_and_01_cannot_match_permission_rule():
    permission_line = next(
        line for line in _rule_text().splitlines()
        if 'GROUP="venus-mouse"' in line
    )
    assert 'bInterfaceNumber}=="02"' in permission_line
    assert 'bInterfaceNumber}=="00"' not in permission_line
    assert 'bInterfaceNumber}=="01"' not in permission_line
    for interface in ("00", "01"):
        assert interface != "02"


def test_rule_avoids_same_parent_attribute_trap():
    rule = _rule_text()
    assert 'ATTRS{idVendor}' not in rule
    assert 'ATTRS{idProduct}' not in rule
    assert 'IMPORT{builtin}="usb_id"' in rule
    assert 'ATTRS{bInterfaceNumber}=="02"' in rule


def test_install_script_has_fixed_target_and_safe_sudo_user_handling():
    script = INSTALLER.read_text(encoding="utf-8")
    assert 'RULE_NAME="70-utechsmart-venus.rules"' in script
    assert 'INSTALL_PATH="/etc/udev/rules.d/${RULE_NAME}"' in script
    assert '${SUDO_USER:-}' in script
    assert '"${SUDO_USER}" == "root"' in script
    assert 'id -u -- "${SUDO_USER}"' in script
    assert 'usermod -a -G "${GROUP_NAME}" "${SUDO_USER}"' in script
    assert "groupadd --system" in script
    assert "MODE=\"plan\"" in script
    assert "--install" in script
    assert "--replace" in script
    assert "udevadm control --reload-rules" in script
    assert "udevadm trigger" not in script
    assert "hidraw" not in script.lower().replace(
        "mode:   0660 on matching hidraw interface 02 only", ""
    ) or "/dev/hidraw*" in script


def test_uninstaller_removes_only_fixed_project_rule():
    script = UNINSTALLER.read_text(encoding="utf-8")
    assert 'INSTALL_PATH="/etc/udev/rules.d/${RULE_NAME}"' in script
    assert 'rm -- "${INSTALL_PATH}"' in script
    assert "rm -r" not in script
    assert "rm -f" not in script
    assert "groupdel" not in script
    assert "gpasswd" not in script
    assert "usermod" not in script
    assert "cmp -s" in script


def test_shell_scripts_are_syntax_valid_and_plan_only():
    subprocess.run(
        ["bash", "-n", str(INSTALLER), str(UNINSTALLER)],
        check=True,
        capture_output=True,
        text=True,
    )
    install = subprocess.run(
        [str(INSTALLER)], check=True, capture_output=True, text=True
    )
    uninstall = subprocess.run(
        [str(UNINSTALLER)], check=True, capture_output=True, text=True
    )
    assert "No changes made" in install.stdout
    assert "No changes made" in uninstall.stdout


def test_udev_parser_accepts_rule_without_resolving_uncreated_group():
    subprocess.run(
        [
            "udevadm", "verify", "--resolve-names=never",
            str(RULE),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_verifier_reports_metadata_without_open_or_ioctl(monkeypatch):
    source = VERIFIER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called_names
    assert "ioctl" not in called_names
    assert "read_memory" not in source
    assert "ReadOnlyHidrawTransport" not in source

    device = HidrawDevice(
        path=Path("/dev/hidraw99"),
        sysfs_path=Path("/mock/sysfs"),
        vendor_id=0x04D9,
        product_id=0xFC55,
        interface_number=2,
        usage_page=0xFFA0,
        usage=1,
    )
    metadata = os.stat_result((
        stat.S_IFCHR | 0o660, 0, 0, 0, os.getuid(), os.getgid(),
        0, 0, 0, 0,
    ))
    result = describe_permissions(
        device,
        stat_fn=lambda path: metadata,
        access_fn=lambda path, mode: True,
    )
    assert result["mode"] == "0660"
    assert result["read_write_access"] is True
    assert result["interface_number"] == "02"


def test_rule_and_helpers_contain_no_mouse_protocol_commands():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (RULE, INSTALLER, UNINSTALLER, VERIFIER)
    ).lower()
    for forbidden in (
        "hid_iocsfeature", "set_feature_report", "write_memory",
        " f1 ", " f2 ", " f3 ", " f5 ",
    ):
        assert forbidden not in combined
