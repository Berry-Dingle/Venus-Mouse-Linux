"""Minimal F2-only hidraw transport for the wired UtechSmart Venus.

Safety boundary:

* only USB interface 2 is discoverable;
* only feature reports 2 and 3 can be retrieved;
* the sole outgoing operation is a validated 16-byte report-2 F2 request;
* there is no public arbitrary feature-report sender.

The implementation uses Linux ``HIDIOCSFEATURE``/``HIDIOCGFEATURE`` ioctls
through :mod:`fcntl`.  It never detaches drivers, claims USB interfaces,
resets devices, or accesses report IDs 4 or 5.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
from typing import Callable, Protocol


VENUS_VENDOR_ID = 0x04D9
VENUS_PRODUCT_ID = 0xFC55
VENUS_INTERFACE = 0x02
VENUS_USAGE_PAGE = 0xFFA0
VENUS_USAGE = 0x0001

F2_COMMAND = 0xF2
REPORT2_ID = 2
REPORT2_LENGTH = 16
REPORT3_ID = 3
REPORT3_LENGTH = 64
RESPONSE_HEADER_LENGTH = 8
MAX_REPORT3_DATA = REPORT3_LENGTH - RESPONSE_HEADER_LENGTH


class HidrawDiscoveryError(RuntimeError):
    """No unique configuration hidraw interface could be selected."""


class HidrawProtocolError(RuntimeError):
    """A request or device response violated the confirmed F2 protocol."""


class HidrawPermissionError(RuntimeError):
    """The selected node could not be opened read/write for feature ioctls."""


@dataclass(frozen=True)
class HidrawDevice:
    path: Path
    sysfs_path: Path
    vendor_id: int
    product_id: int
    interface_number: int
    usage_page: int | None
    usage: int | None

    @property
    def usage_verified(self) -> bool:
        return (
            self.usage_page == VENUS_USAGE_PAGE
            and self.usage == VENUS_USAGE
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sysfs_path": str(self.sysfs_path),
            "vendor_id": f"{self.vendor_id:04x}",
            "product_id": f"{self.product_id:04x}",
            "interface_number": f"{self.interface_number:02x}",
            "usage_page": (
                f"0x{self.usage_page:04X}"
                if self.usage_page is not None else None
            ),
            "usage": self.usage,
            "usage_verified": self.usage_verified,
        }


def _read_hex_attribute(start: Path, name: str) -> int | None:
    for directory in (start, *start.parents):
        path = directory / name
        try:
            return int(path.read_text(encoding="ascii").strip(), 16)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
    return None


def _hid_application_usage(descriptor: bytes) -> tuple[int, int] | None:
    """Return the expected application usage, or the first one if mismatched."""

    offset = 0
    usage_page: int | None = None
    usage: int | None = None
    applications: list[tuple[int, int]] = []
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(descriptor):
                return None
            size = descriptor[offset]
            offset += 2 + size
            continue
        size_code = prefix & 0x03
        size = 4 if size_code == 3 else size_code
        item_type = (prefix >> 2) & 0x03
        tag = (prefix >> 4) & 0x0F
        if offset + size > len(descriptor):
            return None
        data = descriptor[offset:offset + size]
        offset += size
        value = int.from_bytes(data, "little") if data else 0
        if item_type == 1 and tag == 0:       # Global: Usage Page
            usage_page = value
        elif item_type == 2 and tag == 0:     # Local: Usage
            usage = value
        elif item_type == 0 and tag == 10:    # Main: Collection
            collection_type = value & 0xFF
            if collection_type == 1 and usage_page is not None and usage is not None:
                pair = (usage_page, usage)
                applications.append(pair)
                if pair == (VENUS_USAGE_PAGE, VENUS_USAGE):
                    return pair
            usage = None
    return applications[0] if applications else None


def _read_usage(device_path: Path) -> tuple[int, int] | None:
    for directory in (device_path, *device_path.parents):
        descriptor_path = directory / "report_descriptor"
        try:
            descriptor = descriptor_path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError:
            return None
        return _hid_application_usage(descriptor)
    return None


def discover_venus_hidraw(
    *,
    sys_class_hidraw: Path = Path("/sys/class/hidraw"),
    dev_root: Path = Path("/dev"),
) -> HidrawDevice:
    """Find exactly one 04d9:fc55 hidraw node on USB interface 02.

    Interface 00/01 are never fallback candidates.  If a report descriptor is
    readable, its application usage must be page FFA0, usage 1.
    """

    matches: list[HidrawDevice] = []
    try:
        entries = sorted(sys_class_hidraw.iterdir(), key=lambda path: path.name)
    except FileNotFoundError as exc:
        raise HidrawDiscoveryError(
            f"hidraw sysfs class does not exist: {sys_class_hidraw}"
        ) from exc
    for entry in entries:
        if not entry.name.startswith("hidraw"):
            continue
        link = entry / "device"
        try:
            device_path = link.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        vendor = _read_hex_attribute(device_path, "idVendor")
        product = _read_hex_attribute(device_path, "idProduct")
        interface = _read_hex_attribute(device_path, "bInterfaceNumber")
        if (
            vendor != VENUS_VENDOR_ID
            or product != VENUS_PRODUCT_ID
            or interface != VENUS_INTERFACE
        ):
            continue
        usage_pair = _read_usage(device_path)
        if usage_pair is not None and usage_pair != (
            VENUS_USAGE_PAGE, VENUS_USAGE
        ):
            continue
        node = dev_root / entry.name
        if not node.exists():
            continue
        matches.append(HidrawDevice(
            path=node,
            sysfs_path=device_path,
            vendor_id=vendor,
            product_id=product,
            interface_number=interface,
            usage_page=usage_pair[0] if usage_pair else None,
            usage=usage_pair[1] if usage_pair else None,
        ))
    if not matches:
        raise HidrawDiscoveryError(
            "no 04d9:fc55 hidraw node on interface 02 with the expected "
            "vendor usage was found; interfaces 00 and 01 are not accepted"
        )
    if len(matches) != 1:
        paths = ", ".join(str(match.path) for match in matches)
        raise HidrawDiscoveryError(
            f"multiple matching Venus configuration interfaces found: {paths}"
        )
    return matches[0]


def build_f2_request(address: int, length: int) -> bytes:
    if not isinstance(address, int) or isinstance(address, bool):
        raise HidrawProtocolError("address must be an integer")
    if not isinstance(length, int) or isinstance(length, bool):
        raise HidrawProtocolError("requested length must be an integer")
    if not 0 <= address <= 0xFFFF:
        raise HidrawProtocolError("address must be in 0x0000..0xFFFF")
    if length <= 0:
        raise HidrawProtocolError("requested length must be positive")
    if length > MAX_REPORT3_DATA:
        raise HidrawProtocolError(
            f"one read may request at most {MAX_REPORT3_DATA} bytes"
        )
    if address + length - 1 > 0xFFFF:
        raise HidrawProtocolError("read crosses the 16-bit address boundary")
    request = bytes([
        REPORT2_ID,
        F2_COMMAND,
        address & 0xFF,
        address >> 8,
        length & 0xFF,
        length >> 8,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ])
    validate_f2_request(request)
    return request


def validate_f2_request(request: bytes) -> tuple[int, int]:
    """Validate every outgoing byte before the device boundary."""

    if len(request) != REPORT2_LENGTH:
        raise HidrawProtocolError("outgoing F2 request must be exactly 16 bytes")
    if request[0] != REPORT2_ID:
        raise HidrawProtocolError("outgoing report ID must be 2")
    if request[1] != F2_COMMAND:
        raise HidrawProtocolError("outgoing command must be F2")
    if request[6:] != b"\x00" * 10:
        raise HidrawProtocolError("outgoing F2 bytes 6..15 must be zero")
    address = int.from_bytes(request[2:4], "little")
    length = int.from_bytes(request[4:6], "little")
    if length <= 0:
        raise HidrawProtocolError("requested length must be positive")
    if length > MAX_REPORT3_DATA:
        raise HidrawProtocolError(
            f"one read may request at most {MAX_REPORT3_DATA} bytes"
        )
    if address + length - 1 > 0xFFFF:
        raise HidrawProtocolError("read crosses the 16-bit address boundary")
    return address, length


def response_report_for_length(length: int) -> tuple[int, int]:
    if not 1 <= length <= MAX_REPORT3_DATA:
        raise HidrawProtocolError(
            f"response length must be 1..{MAX_REPORT3_DATA}"
        )
    return (
        (REPORT2_ID, REPORT2_LENGTH)
        if length <= REPORT2_LENGTH - RESPONSE_HEADER_LENGTH
        else (REPORT3_ID, REPORT3_LENGTH)
    )


def validate_f2_response(
    response: bytes,
    *,
    address: int,
    requested_length: int,
) -> bytes:
    expected_report_id, maximum_length = response_report_for_length(
        requested_length
    )
    if len(response) > maximum_length:
        raise HidrawProtocolError(
            f"response is longer than report {expected_report_id}"
        )
    needed = RESPONSE_HEADER_LENGTH + requested_length
    if len(response) < needed:
        raise HidrawProtocolError(
            f"short response: received {len(response)}, need at least {needed}"
        )
    if response[0] not in {REPORT2_ID, REPORT3_ID}:
        raise HidrawProtocolError(
            f"unexpected response report ID {response[0]}"
        )
    if response[0] != expected_report_id:
        raise HidrawProtocolError(
            f"response report ID {response[0]} does not match requested "
            f"length {requested_length}"
        )
    if response[1] != 0x08:
        raise HidrawProtocolError(
            f"unexpected response status byte 0x{response[1]:02X}"
        )
    transformed = (address & ~1)
    expected_low = transformed & 0xFF
    expected_high = ((transformed >> 8) + 0x60) & 0xFF
    if response[2:4] != bytes([expected_low, expected_high]):
        raise HidrawProtocolError(
            "response transformed address does not match request"
        )
    if int.from_bytes(response[4:6], "little") != requested_length:
        raise HidrawProtocolError("response length field does not match request")
    if response[6:8] != b"\xFA\xFA":
        raise HidrawProtocolError("response marker is not FA FA")
    return response[8:needed]


def _ioc(direction: int, type_value: int, number: int, size: int) -> int:
    return (
        (direction << 30)
        | (type_value << 8)
        | number
        | (size << 16)
    )


def _hid_iocsfeature(length: int) -> int:
    return _ioc(3, ord("H"), 0x06, length)


def _hid_iogfeature(length: int) -> int:
    return _ioc(3, ord("H"), 0x07, length)


class _FeatureIo(Protocol):
    def send_f2(self, request: bytes) -> None: ...
    def get_feature(self, report_id: int, length: int) -> bytes: ...
    def close(self) -> None: ...


class _HidrawFeatureIo:
    def __init__(
        self,
        path: Path,
        *,
        opener: Callable[[str, int], int] = os.open,
        ioctl: Callable[..., int] = fcntl.ioctl,
    ) -> None:
        self.path = path
        self._ioctl = ioctl
        try:
            self._fd = opener(
                str(path), os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
            )
        except PermissionError as exc:
            raise HidrawPermissionError(
                f"read/write permission is required for feature ioctls on {path}"
            ) from exc
        except OSError as exc:
            raise HidrawPermissionError(
                f"could not open selected hidraw node {path}: {exc}"
            ) from exc

    def send_f2(self, request: bytes) -> None:
        validate_f2_request(request)
        buffer = bytearray(request)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise HidrawProtocolError(
                f"F2 SET_FEATURE returned {result}, expected {REPORT2_LENGTH}"
            )

    def get_feature(self, report_id: int, length: int) -> bytes:
        buffer = bytearray(length)
        buffer[0] = report_id
        result = self._ioctl(
            self._fd, _hid_iogfeature(length), buffer, True
        )
        if not isinstance(result, int) or not 1 <= result <= length:
            raise HidrawProtocolError(
                f"GET_FEATURE returned invalid byte count {result}"
            )
        return bytes(buffer[:result])

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


class ReadOnlyHidrawTransport:
    """Public F2-only memory reader.

    The only live public operations are ``get_feature_report`` and
    ``read_memory``.  The backend is opened lazily, after request validation.
    """

    def __init__(
        self,
        device: HidrawDevice,
        *,
        backend_factory: Callable[[Path], _FeatureIo] = _HidrawFeatureIo,
    ) -> None:
        if device.interface_number != VENUS_INTERFACE:
            raise HidrawDiscoveryError("transport requires USB interface 02")
        self.device = device
        self._backend_factory = backend_factory
        self._backend_instance: _FeatureIo | None = None
        self._last_response: bytes | None = None

    @property
    def last_response(self) -> bytes | None:
        """Exact most recent GET_FEATURE bytes, for transaction logging."""
        return self._last_response

    def _backend(self) -> _FeatureIo:
        if self._backend_instance is None:
            self._backend_instance = self._backend_factory(self.device.path)
        return self._backend_instance

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        if (report_id, length) not in {
            (REPORT2_ID, REPORT2_LENGTH),
            (REPORT3_ID, REPORT3_LENGTH),
        }:
            raise HidrawProtocolError(
                "only report 2/16-byte and report 3/64-byte GET_FEATURE "
                "operations are allowed"
            )
        return self._backend().get_feature(report_id, length)

    def _send_f2_request(self, request: bytes) -> None:
        validate_f2_request(request)
        self._backend().send_f2(request)

    def read_memory(self, address: int, length: int) -> bytes:
        request = build_f2_request(address, length)
        # build_f2_request/validate_f2_request run before lazy device opening.
        self._send_f2_request(request)
        report_id, report_length = response_report_for_length(length)
        response = self.get_feature_report(report_id, report_length)
        self._last_response = response
        return validate_f2_response(
            response, address=address, requested_length=length
        )

    def close(self) -> None:
        if self._backend_instance is not None:
            self._backend_instance.close()
            self._backend_instance = None

    def __enter__(self) -> "ReadOnlyHidrawTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
