"""Strictly offline parser for Venus USB capture files.

This module reads saved Wireshark JSON or invokes ``tshark`` on an existing
pcap/pcapng file.  It contains no HID imports, device discovery, hidraw access,
or report-writing functionality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable

from .actions import ActionRecord

FRAME_FIRST = 24100
FRAME_LAST = 24877
DEVICE_ADDRESS = 5
INTERFACE_INDEX = 2
FEATURE_REPORT_TYPE = 3
USB_CONTROL_TRANSFER = 0x02
REPORT2_HEADER_SIZE = 8


class CaptureFormatError(ValueError):
    """The export lacks data required for a lossless offline decode."""


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0]
    return value


def _hex_bytes(value: Any) -> bytes:
    if not value:
        return b""
    return bytes.fromhex(str(_scalar(value)).replace(":", ""))


def _integer(value: Any) -> int:
    text = str(_scalar(value))
    return int(text, 0)


def _layers(packet: dict[str, Any]) -> dict[str, Any]:
    try:
        return packet["_source"]["layers"]
    except (KeyError, TypeError) as exc:
        raise CaptureFormatError("packet is not a Wireshark JSON packet") from exc


def load_wireshark_packets(
    path: Path,
    *,
    first_frame: int | None = FRAME_FIRST,
    last_frame: int | None = FRAME_LAST,
    device_address: int = DEVICE_ADDRESS,
) -> list[dict[str, Any]]:
    """Load packets from JSON, or ask tshark to decode a saved capture.

    ``tshark`` is used only as an offline file decoder.  ``-x`` is essential:
    ordinary Wireshark JSON may omit control-IN response bytes.
    """

    path = Path(path)
    if path.suffix.lower() == ".json":
        result = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() in {".pcap", ".pcapng"}:
        filters = [
            f"usb.device_address == {device_address}",
            f"usb.transfer_type == 0x{USB_CONTROL_TRANSFER:02x}",
        ]
        if first_frame is not None:
            filters.append(f"frame.number >= {first_frame}")
        if last_frame is not None:
            filters.append(f"frame.number <= {last_frame}")
        display_filter = " && ".join(filters)
        completed = subprocess.run(
            [
                "tshark", "-r", str(path), "-Y", display_filter,
                "-T", "json", "-x",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
    else:
        raise CaptureFormatError(f"unsupported capture/export suffix: {path.suffix}")
    if not isinstance(result, list):
        raise CaptureFormatError("Wireshark JSON root must be a packet list")
    return result


def _completion_payload(layers: dict[str, Any]) -> bytes:
    usb = layers.get("usb", {})
    data_length = _integer(usb.get("usb.data_len", "0"))
    if data_length == 0:
        return b""
    for key in ("usb.capdata", "usb.data_fragment", "data.data"):
        value = usb.get(key) or layers.get(key)
        if value:
            data = _hex_bytes(value)
            if len(data) >= data_length:
                return data[-data_length:]
    raw_value = layers.get("frame_raw")
    if raw_value:
        raw = _hex_bytes(raw_value)
        if len(raw) >= data_length:
            # Linux usbmon records have a 64-byte pseudo-header.  Taking the
            # declared data_len from the end also handles truncated captures
            # more safely than hard-coding that header size.
            return raw[-data_length:]
    raise CaptureFormatError(
        "control-IN completion has data_len but no exported raw packet bytes; "
        "use the pcapng or Wireshark JSON exported with -x"
    )


@dataclass(frozen=True)
class ControlTransfer:
    submission_frame: int
    completion_frame: int
    timestamp: str
    relative_time: float
    urb_id: str
    request_in: int | None
    request_type: int
    request: int
    report_id: int
    report_type: int
    interface_index: int
    requested_report_length: int
    completion_data_length: int
    completion_status: int
    payload: bytes

    @property
    def direction(self) -> str:
        return "SET_REPORT" if self.request == 0x09 else "GET_REPORT"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["payload"] = self.payload.hex(" ")
        result["direction"] = self.direction
        return result


@dataclass(frozen=True)
class FeatureRead:
    set_transfer: ControlTransfer
    get_transfer: ControlTransfer
    command: int
    address: int
    requested_length: int
    response: bytes
    returned_data: bytes

    @property
    def response_report_id(self) -> int:
        return self.response[0]

    @property
    def transformed_address(self) -> int:
        return self.response[2] | ((self.response[3] - 0x60) << 8)

    def validate_response(self) -> list[str]:
        errors: list[str] = []
        expected_size = REPORT2_HEADER_SIZE + self.requested_length
        if len(self.response) != expected_size:
            errors.append(
                f"response size {len(self.response)} != expected {expected_size}"
            )
            return errors
        expected_report = 2 if self.requested_length <= 7 else 3
        if self.response[0] != expected_report:
            errors.append(
                f"response report ID {self.response[0]} != {expected_report}"
            )
        if self.response[1] != 0x08:
            errors.append(f"response command/status is 0x{self.response[1]:02X}")
        if self.transformed_address != (self.address & ~1):
            errors.append(
                f"transformed address 0x{self.transformed_address:04X} != "
                f"0x{self.address & ~1:04X}"
            )
        if int.from_bytes(self.response[4:6], "little") != self.requested_length:
            errors.append("returned-length field does not match request")
        if self.response[6:8] != b"\xFA\xFA":
            errors.append("response marker is not FA FA")
        if self.returned_data != self.response[8:]:
            errors.append("returned-data extraction mismatch")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_submission_frame": self.set_transfer.submission_frame,
            "set_completion_frame": self.set_transfer.completion_frame,
            "get_submission_frame": self.get_transfer.submission_frame,
            "get_completion_frame": self.get_transfer.completion_frame,
            "timestamp": self.set_transfer.timestamp,
            "command": f"{self.command:02X}",
            "address": self.address,
            "address_hex": f"0x{self.address:04X}",
            "requested_length": self.requested_length,
            "set_payload": self.set_transfer.payload.hex(" "),
            "get_report_id": self.get_transfer.report_id,
            "response": self.response.hex(" "),
            "response_report_id": self.response_report_id,
            "response_status": self.response[1],
            "transformed_address": self.transformed_address,
            "returned_length": int.from_bytes(self.response[4:6], "little"),
            "marker": self.response[6:8].hex(" "),
            "returned_data": self.returned_data.hex(" "),
            "response_errors": self.validate_response(),
        }


@dataclass(frozen=True)
class FeatureWrite:
    """One address-bearing F3 SET_REPORT transaction."""

    transfer: ControlTransfer
    command: int
    address: int
    block_length: int
    header_reserved: bytes
    data: bytes
    padding: bytes

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.transfer.direction != "SET_REPORT":
            errors.append("F3 transaction is not SET_REPORT")
        if self.transfer.payload[0] != self.transfer.report_id:
            errors.append("payload report ID differs from setup report ID")
        if self.command != 0xF3:
            errors.append(f"command is 0x{self.command:02X}, not F3")
        if self.header_reserved != b"\x00\x00":
            errors.append(
                f"header reserved bytes are {self.header_reserved.hex(' ')}"
            )
        if len(self.data) != self.block_length:
            errors.append(
                f"decoded data length {len(self.data)} != {self.block_length}"
            )
        if any(self.padding):
            errors.append("non-zero bytes occur after declared F3 payload")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_frame": self.transfer.submission_frame,
            "completion_frame": self.transfer.completion_frame,
            "timestamp": self.transfer.timestamp,
            "relative_time": self.transfer.relative_time,
            "report_id": self.transfer.report_id,
            "report_type": self.transfer.report_type,
            "interface_index": self.transfer.interface_index,
            "declared_transfer_length": self.transfer.requested_report_length,
            "actual_data_length": self.transfer.completion_data_length,
            "complete_payload": self.transfer.payload.hex(" "),
            "command": f"{self.command:02X}",
            "address": self.address,
            "address_hex": f"0x{self.address:04X}",
            "block_length": self.block_length,
            "header_reserved": self.header_reserved.hex(" "),
            "data": self.data.hex(" "),
            "padding_length": len(self.padding),
            "padding": self.padding.hex(" "),
            "validation_errors": self.validate(),
        }


@dataclass(frozen=True)
class DeviceMacroSlot:
    """Lossless decode of one 200-byte device macro slot."""

    address: int
    raw: bytes
    records: tuple[ActionRecord, ...]
    trailing_bytes: bytes

    @property
    def populated_record_count(self) -> int:
        last = -1
        for index, record in enumerate(self.records):
            if record.raw != b"\x00\x00\x00":
                last = index
        return last + 1

    @property
    def populated_records(self) -> tuple[ActionRecord, ...]:
        return self.records[:self.populated_record_count]

    @property
    def file_style_additive_checksum(self) -> int:
        """Checksum a file MacroSetting payload would store after 189 bytes."""
        return sum(self.raw[:189]) & 0xFF

    @property
    def byte_189(self) -> int:
        return self.raw[189]

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "address_hex": f"0x{self.address:04X}",
            "raw_length": len(self.raw),
            "raw": self.raw.hex(" "),
            "record_capacity": len(self.records),
            "populated_record_count": self.populated_record_count,
            "populated_records": [
                {
                    "index": index,
                    "offset": index * 3,
                    "raw": record.raw.hex(" "),
                    "description": record.describe(),
                }
                for index, record in enumerate(self.populated_records)
            ],
            "zero_record_count": sum(
                record.raw == b"\x00\x00\x00" for record in self.records
            ),
            "trailing_bytes": self.trailing_bytes.hex(" "),
            "file_style_additive_checksum": self.file_style_additive_checksum,
            "byte_189": self.byte_189,
            "file_style_checksum_present": (
                self.byte_189 == self.file_style_additive_checksum
            ),
        }


@dataclass(frozen=True)
class DeviceMacroAssignment:
    """Four-byte compiled device button assignment for a macro."""

    raw: bytes

    def __post_init__(self) -> None:
        if len(self.raw) != 4:
            raise ValueError("device button assignment must be four bytes")

    @property
    def function_code(self) -> int:
        return self.raw[0]

    @property
    def slot_and_flags(self) -> int:
        return self.raw[1]

    @property
    def slot_index(self) -> int:
        return self.slot_and_flags & 0x3F

    @property
    def while_held(self) -> bool:
        return bool(self.slot_and_flags & 0x80)

    @property
    def toggle(self) -> bool:
        return bool(self.slot_and_flags & 0x40)

    @property
    def execution_count(self) -> int:
        return int.from_bytes(self.raw[2:4], "little")

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw.hex(" "),
            "function_code": self.function_code,
            "function_code_hex": f"0x{self.function_code:02X}",
            "slot_and_flags": self.slot_and_flags,
            "slot_index": self.slot_index,
            "while_held": self.while_held,
            "toggle": self.toggle,
            "execution_count": self.execution_count,
        }


def decode_device_macro_slot(data: bytes, *, address: int) -> DeviceMacroSlot:
    if len(data) != 200:
        raise ValueError("device macro slot must be exactly 200 bytes")
    return DeviceMacroSlot(
        address=address,
        raw=data,
        records=tuple(
            ActionRecord(data[offset:offset + 3])
            for offset in range(0, 189, 3)
        ),
        trailing_bytes=data[189:],
    )


@dataclass
class MemoryImage:
    values: dict[int, int]
    overlaps: list[dict[str, int]]

    @property
    def minimum_address(self) -> int:
        return min(self.values)

    @property
    def maximum_address(self) -> int:
        return max(self.values)

    def ranges(self) -> list[tuple[int, int]]:
        addresses = sorted(self.values)
        if not addresses:
            return []
        result: list[tuple[int, int]] = []
        start = previous = addresses[0]
        for address in addresses[1:]:
            if address != previous + 1:
                result.append((start, previous))
                start = address
            previous = address
        result.append((start, previous))
        return result

    def gaps(self) -> list[tuple[int, int]]:
        ranges = self.ranges()
        return [
            (ranges[index][1] + 1, ranges[index + 1][0] - 1)
            for index in range(len(ranges) - 1)
        ]

    def flat_bytes(self, fill: int = 0) -> bytes:
        """Return min..max bytes; use the memory map to distinguish gap fill."""
        return bytes(
            self.values.get(address, fill)
            for address in range(self.minimum_address, self.maximum_address + 1)
        )


def extract_control_transfers(
    packets: Iterable[dict[str, Any]],
    *,
    first_frame: int | None = FRAME_FIRST,
    last_frame: int | None = FRAME_LAST,
    device_address: int = DEVICE_ADDRESS,
    interface_index: int = INTERFACE_INDEX,
) -> list[ControlTransfer]:
    submissions: dict[str, tuple[int, dict[str, Any]]] = {}
    transfers: list[ControlTransfer] = []
    for packet in packets:
        layers = _layers(packet)
        usb = layers.get("usb")
        frame = layers.get("frame")
        if not isinstance(usb, dict) or not isinstance(frame, dict):
            continue
        if _integer(usb.get("usb.device_address", "-1")) != device_address:
            continue
        if _integer(usb.get("usb.transfer_type", "-1")) != USB_CONTROL_TRANSFER:
            continue
        urb_id = str(_scalar(usb.get("usb.urb_id", "")))
        urb_type = str(_scalar(usb.get("usb.urb_type", ""))).strip("'")
        frame_number = _integer(frame["frame.number"])
        if first_frame is not None and frame_number < first_frame:
            continue
        if last_frame is not None and frame_number > last_frame:
            continue
        if urb_type == "S":
            submissions[urb_id] = (frame_number, layers)
            continue
        if urb_type != "C" or urb_id not in submissions:
            continue
        submission_frame, submission = submissions[urb_id]
        setup = submission.get("Setup Data")
        if not isinstance(setup, dict):
            continue
        request = _integer(setup.get("usbhid.setup.bRequest", "-1"))
        if request not in {0x01, 0x09}:
            continue
        report_value = _integer(setup["usbhid.setup.wValue"])
        report_id = report_value & 0xFF
        report_type = report_value >> 8
        interface_index = _integer(setup["usbhid.setup.wIndex"])
        parsed_interface = _integer(setup["usbhid.setup.wIndex"])
        if report_type != FEATURE_REPORT_TYPE or parsed_interface != interface_index:
            continue
        payload = (
            _hex_bytes(setup.get("usb.data_fragment"))
            if request == 0x09
            else _completion_payload(layers)
        )
        request_in_raw = usb.get("usb.request_in")
        request_in = _integer(request_in_raw) if request_in_raw is not None else None
        transfers.append(
            ControlTransfer(
                submission_frame=submission_frame,
                completion_frame=frame_number,
                timestamp=str(_scalar(submission["frame"].get("frame.time", ""))),
                relative_time=float(
                    _scalar(submission["frame"].get("frame.time_relative", "0"))
                ),
                urb_id=urb_id,
                request_in=request_in,
                request_type=_integer(setup["usb.bmRequestType"]),
                request=request,
                report_id=report_id,
                report_type=report_type,
                interface_index=parsed_interface,
                requested_report_length=_integer(setup["usbhid.setup.wLength"]),
                completion_data_length=len(payload),
                completion_status=_integer(usb.get("usb.urb_status", "0")),
                payload=payload,
            )
        )
    return sorted(transfers, key=lambda transfer: transfer.submission_frame)


def pair_feature_reads(transfers: Iterable[ControlTransfer]) -> list[FeatureRead]:
    ordered = list(transfers)
    reads: list[FeatureRead] = []
    for index, transfer in enumerate(ordered):
        if transfer.direction != "SET_REPORT" or len(transfer.payload) < 6:
            continue
        if transfer.payload[0] not in {2, 3} or transfer.payload[1] != 0xF2:
            continue
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        if following is None or following.direction != "GET_REPORT":
            raise CaptureFormatError(
                f"F2 SET_REPORT frame {transfer.submission_frame} is not "
                "followed by a GET_REPORT transfer"
            )
        address = int.from_bytes(transfer.payload[2:4], "little")
        requested_length = int.from_bytes(transfer.payload[4:6], "little")
        if len(following.payload) < REPORT2_HEADER_SIZE:
            raise CaptureFormatError(
                f"GET_REPORT completion frame {following.completion_frame} "
                "does not contain the response bytes"
            )
        read = FeatureRead(
            set_transfer=transfer,
            get_transfer=following,
            command=0xF2,
            address=address,
            requested_length=requested_length,
            response=following.payload,
            returned_data=following.payload[
                REPORT2_HEADER_SIZE:REPORT2_HEADER_SIZE + requested_length
            ],
        )
        errors = read.validate_response()
        if errors:
            raise CaptureFormatError(
                f"invalid response at frame {following.completion_frame}: "
                + "; ".join(errors)
            )
        reads.append(read)
    return reads


def decode_feature_writes(
    transfers: Iterable[ControlTransfer],
) -> list[FeatureWrite]:
    writes: list[FeatureWrite] = []
    for transfer in transfers:
        payload = transfer.payload
        if (
            transfer.direction != "SET_REPORT"
            or len(payload) < REPORT2_HEADER_SIZE
            or payload[1] != 0xF3
        ):
            continue
        block_length = int.from_bytes(payload[4:6], "little")
        available = len(payload) - REPORT2_HEADER_SIZE
        if block_length > available:
            raise CaptureFormatError(
                f"F3 frame {transfer.submission_frame} declares "
                f"{block_length} bytes but report holds only {available}"
            )
        write = FeatureWrite(
            transfer=transfer,
            command=payload[1],
            address=int.from_bytes(payload[2:4], "little"),
            block_length=block_length,
            header_reserved=payload[6:8],
            data=payload[8:8 + block_length],
            padding=payload[8 + block_length:],
        )
        errors = write.validate()
        if errors:
            raise CaptureFormatError(
                f"invalid F3 frame {transfer.submission_frame}: "
                + "; ".join(errors)
            )
        writes.append(write)
    return writes


def reconstruct_written_memory(
    writes: Iterable[FeatureWrite],
) -> MemoryImage:
    values: dict[int, int] = {}
    overlaps: list[dict[str, int]] = []
    for write in writes:
        for offset, value in enumerate(write.data):
            address = write.address + offset
            if address in values:
                overlaps.append(
                    {
                        "address": address,
                        "previous": values[address],
                        "new": value,
                        "agree": int(values[address] == value),
                    }
                )
            values[address] = value
    return MemoryImage(values, overlaps)


def reconstruct_memory(reads: Iterable[FeatureRead]) -> MemoryImage:
    values: dict[int, int] = {}
    overlaps: list[dict[str, int]] = []
    for read in reads:
        for offset, value in enumerate(read.returned_data):
            address = read.address + offset
            if address in values:
                overlaps.append(
                    {
                        "address": address,
                        "previous": values[address],
                        "new": value,
                        "agree": int(values[address] == value),
                    }
                )
            values[address] = value
    return MemoryImage(values, overlaps)


def analyse_capture(path: Path) -> tuple[
    list[ControlTransfer], list[FeatureRead], MemoryImage
]:
    transfers = extract_control_transfers(load_wireshark_packets(path))
    reads = pair_feature_reads(transfers)
    return transfers, reads, reconstruct_memory(reads)


def analyse_feature_capture(
    path: Path,
    *,
    first_frame: int | None = None,
    last_frame: int | None = None,
    device_address: int = DEVICE_ADDRESS,
    interface_index: int = INTERFACE_INDEX,
) -> tuple[list[ControlTransfer], list[FeatureRead], list[FeatureWrite]]:
    """Decode all feature transfers in an arbitrary saved capture."""

    packets = load_wireshark_packets(
        path,
        first_frame=first_frame,
        last_frame=last_frame,
        device_address=device_address,
    )
    transfers = extract_control_transfers(
        packets,
        first_frame=first_frame,
        last_frame=last_frame,
        device_address=device_address,
        interface_index=interface_index,
    )
    return transfers, pair_feature_reads(transfers), decode_feature_writes(transfers)
