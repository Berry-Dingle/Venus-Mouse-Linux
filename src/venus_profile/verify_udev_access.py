"""Metadata-only verification of Venus configuration-node permissions.

This command enumerates sysfs and performs stat/access checks. It never opens
the hidraw node, invokes an ioctl, or sends a HID report.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .device_session import DeviceSession
from .readonly_hidraw import HidrawDevice


def describe_permissions(
    device: HidrawDevice,
    *,
    stat_fn: Callable[[Path], os.stat_result] = Path.stat,
    access_fn: Callable[[Path, int], bool] = os.access,
) -> dict[str, object]:
    session = DeviceSession(
        device, stat_fn=stat_fn, access_fn=access_fn
    )
    access = session.check_access()
    return {
        "path": str(device.path),
        "vendor_id": f"{device.vendor_id:04x}",
        "product_id": f"{device.product_id:04x}",
        "interface_number": f"{device.interface_number:02x}",
        "usage_page": (
            f"0x{device.usage_page:04X}"
            if device.usage_page is not None else None
        ),
        "usage": device.usage,
        **access.to_dict(),
    }


def main(
    *,
    session_factory: Callable[[], DeviceSession] = DeviceSession.discover,
) -> int:
    session = session_factory()
    device = session.device
    details = {**device.to_dict(), **session.check_access().to_dict()}
    print(
        f"{details['path']}: "
        f"VID:PID={details['vendor_id']}:{details['product_id']} "
        f"interface={details['interface_number']} "
        f"usage={details['usage_page']}/{details['usage']}"
    )
    print(
        f"owner={details['owner']} group={details['group']} "
        f"mode={details['mode']}"
    )
    print(
        "current-user read/write access: "
        + ("yes" if details["read_write_access"] else "no")
    )
    return 0 if details["read_write_access"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
