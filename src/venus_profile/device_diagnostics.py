"""Metadata-only Venus session diagnostics; no HID operation is performed."""

from __future__ import annotations

import grp
import json
import os
from pathlib import Path
from typing import Callable

from .device_session import DeviceSession, ownership_warnings
from .known_device_state import KNOWN_STATE_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGED_RULE = (
    PROJECT_ROOT / "linux-suite-prototype" / "packaging" / "udev"
    / "70-utechsmart-venus.rules"
)
INSTALLED_RULE = Path("/etc/udev/rules.d/70-utechsmart-venus.rules")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "usb-captures"


def venus_group_active(
    *,
    group_ids: list[int] | None = None,
) -> bool:
    try:
        venus_gid = grp.getgrnam("venus-mouse").gr_gid
    except KeyError:
        return False
    return venus_gid in (os.getgroups() if group_ids is None else group_ids)


def diagnostics(
    session: DeviceSession,
    *,
    installed_rule: Path = INSTALLED_RULE,
    packaged_rule: Path = PACKAGED_RULE,
    state_path: Path = KNOWN_STATE_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    group_active_fn: Callable[[], bool] = venus_group_active,
) -> dict[str, object]:
    access = session.check_access()
    installed = installed_rule.is_file()
    packaged = packaged_rule.is_file()
    identical = (
        installed_rule.read_bytes() == packaged_rule.read_bytes()
        if installed and packaged else None
    )
    return {
        "device": session.describe(),
        "access": access.to_dict(),
        "venus_mouse_group_active": group_active_fn(),
        "expected_interface": "02",
        "expected_usage_page": "0xFFA0",
        "expected_usage": 1,
        "udev": {
            "packaged_rule": str(packaged_rule),
            "packaged_rule_readable": packaged,
            "installed_rule": str(installed_rule),
            "installed_rule_readable": installed,
            "installed_matches_packaged": identical,
        },
        "warnings": ownership_warnings(
            [state_path, output_root],
            sudo_user=os.environ.get("SUDO_USER"),
        ),
        "hid_operation_performed": False,
    }


def main(
    *,
    session_factory: Callable[[], DeviceSession] = DeviceSession.discover,
) -> int:
    document = diagnostics(session_factory())
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
