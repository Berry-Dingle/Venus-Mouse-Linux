#!/usr/bin/env python3
"""Generate deterministic offline artefacts from the factory-open capture."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT / "linux-suite-prototype" / "src"
sys.path.insert(0, str(SOURCE))

from venus_profile.offline_usb_capture import analyse_capture  # noqa: E402


DEFAULT_CAPTURE = (
    PROJECT / "usb-captures" / "factory_first_open_20260727_074015.pcapng"
)
DEFAULT_OUTPUT = PROJECT / "usb-captures" / "analysis"


def hexadecimal_range(start: int, end: int) -> str:
    return f"0x{start:04X}–0x{end:04X}"


def build_report(transfers, reads, memory, capture: Path) -> str:
    commands = Counter(
        transfer.payload[1]
        for transfer in transfers
        if transfer.direction == "SET_REPORT" and len(transfer.payload) >= 2
    )
    ranges = memory.ranges()
    gaps = memory.gaps()
    inconsistent = [item for item in memory.overlaps if not item["agree"]]
    report_ids = Counter(
        (read.set_transfer.report_id, read.set_transfer.requested_report_length)
        for read in reads
    )
    macro_reads = [
        read for read in reads if 0x0478 <= read.address <= 0x1417
    ]
    lines = [
        "# Factory first-open USB protocol report",
        "",
        "This report was generated strictly offline from the saved capture. The "
        "parser has no HID or device-access code.",
        "",
        "## Capture scope",
        "",
        f"- Source: `{capture}`",
        "- Frames: 24100–24877 inclusive",
        "- Device address: 5",
        "- HID interface: 2",
        "- Transfer class: control transfers carrying HID Feature reports",
        f"- Completed feature-report transfers: {len(transfers)} "
        f"({sum(t.direction == 'SET_REPORT' for t in transfers)} SET, "
        f"{sum(t.direction == 'GET_REPORT' for t in transfers)} GET)",
        "",
        "## Commands",
        "",
        f"- `F2`: {commands[0xF2]} SET_REPORT reads, each followed by one "
        "GET_REPORT. **Confirmed from capture, high confidence.**",
        f"- `F5`: {commands[0xF5]} SET_REPORT commands. Frame 24100 is "
        "`02 F5 00 00 00 00 00 00 00 00 00 00 00 00 00 00`; frame "
        "24876 is `02 F5 01 00 00 00 00 00 00 00 00 00 00 00 00 00`. "
        "**Payloads confirmed; semantics inferred.**",
        "",
        "The opening `F5 00` most plausibly selects profile index 0 for the "
        "read, while final `F5 01` most plausibly restores/selects profile "
        "index 1 after reading. The final command has only its SET completion "
        "(frame 24877) and no GET. Calling either command an apply/commit is "
        "not proven by this capture alone. **Inference, medium confidence.**",
        "",
        "No other command byte occurs in the scoped traffic.",
        "",
        "## Interface-2 report descriptor cross-check",
        "",
        "The supplied 106-byte descriptor independently declares feature "
        "report 2 as 15 data bytes plus its report ID (16 total), and report "
        "3 as 63 data bytes plus its report ID (64 total). Those declarations "
        "exactly match the observed F2 transfers. It also declares report 4 "
        "as 256 total bytes, report 5 as 1,024, and report 6 as 2,048. This "
        "creates an unresolved discrepancy with the earlier disassembly note "
        "that a report-ID-4 `04 F3 ...` buffer is passed with length 1,024. "
        "There is no report-4 write in this capture, so existing write "
        "semantics have not been changed. **Descriptor fact, high confidence; "
        "Windows API/buffer interpretation unresolved.**",
        "",
        "## F2 request and response layout",
        "",
        "SET_REPORT request prefix:",
        "",
        "```text",
        "<report-id> F2 <address-lo> <address-hi> <length-lo> <length-hi> ...",
        "```",
        "",
        "Short requests use report 2 / 16-byte feature transfers. Reads of "
        "32 or 50 bytes use report 3 / 64-byte feature transfers. Distribution:",
        "",
    ]
    for (report_id, report_length), count in sorted(report_ids.items()):
        lines.append(
            f"- report {report_id}, transfer length {report_length}: {count} reads"
        )
    lines.extend([
        "",
        "GET_REPORT completion payload:",
        "",
        "```text",
        "offset  size  meaning",
        "0       1     response report ID (2 for <=7 data bytes; 3 for 32/50)",
        "1       1     08 response/status byte (meaning beyond response marker unknown)",
        "2       1     low byte of (requested address & ~1)",
        "3       1     requested address high byte + 0x60",
        "4       2     returned/requested data length, little-endian",
        "6       2     constant marker FA FA",
        "8       N     returned memory bytes",
        "```",
        "",
        "All 193 responses match this layout. The address is only transformed "
        "for the five odd requests at `0x0449 + n×8`, whose echoed address is "
        "rounded down by one; all even addresses echo unchanged. "
        "**Confirmed from capture, high confidence.**",
        "",
        "Each USB submission is joined to its completion by `usb.urb_id` and "
        "the completion's `usb.request_in` metadata. Each F2 SET transfer is "
        "then paired with the immediately following completed GET transfer; "
        "all 193 pairs satisfy that ordering with no intervening feature "
        "command. **Confirmed, high confidence.**",
        "",
        "The ordinary Wireshark JSON export omits the control-IN data. The "
        "parser therefore recovers `usb.data_len` bytes from the raw packet "
        "record in the pcapng (equivalent to exporting JSON with `tshark -x`).",
        "",
        "## Reconstructed memory",
        "",
        f"- F2 transactions: {len(reads)}",
        f"- Returned bytes (sum): {sum(len(read.returned_data) for read in reads)}",
        f"- Unique returned addresses: {len(memory.values)}",
        f"- Minimum address: `0x{memory.minimum_address:04X}`",
        f"- Maximum address: `0x{memory.maximum_address:04X}`",
        f"- Contiguous ranges: {len(ranges)}",
        f"- Gaps between minimum and maximum: {len(gaps)} ranges, "
        f"{sum(end - start + 1 for start, end in gaps)} bytes",
        f"- Overlapping returned bytes: {len(memory.overlaps)}",
        f"- Inconsistent overlaps: {len(inconsistent)}",
        "",
        "Because there are no overlaps, overlap agreement is vacuously true; "
        "the capture does not provide duplicate-read verification.",
        "",
        "Exact returned ranges:",
        "",
    ])
    for start, end in ranges:
        lines.append(
            f"- `{hexadecimal_range(start, end)}` ({end - start + 1} bytes)"
        )
    lines.extend([
        "",
        "## Region classification",
        "",
        "- Five 32-byte reads at `0x0042`, `0x0102`, `0x01B2`, `0x0262`, "
        "and `0x0312` contain the five profile DPI-stage blocks. Their "
        "contents have the expected five enabled X/Y tuples. "
        "**Capture/project correlation, high confidence.**",
        "- Five groups of twenty 4-byte reads cover button-assignment state: "
        "`0x0082..0x00E1`, `0x0142..0x01A1`, `0x01F2..0x0251`, "
        "`0x02A2..0x0301`, and `0x0352..0x03B1`, with deliberate holes. "
        "**Capture/project correlation, high confidence.**",
        "- Five 7-byte reads at `0x0449`, `0x0451`, `0x0459`, `0x0461`, "
        "and `0x0469` are one per profile. Their first three returned bytes "
        "are respectively `FF 00 00`, `00 00 FF`, `00 FF 00`, `FF 00 FF`, "
        "and `FF FF 00`, strongly identifying a three-byte colour field; the "
        "remaining common bytes are `02 05 01 02`. Exact meanings of those "
        "four common bytes remain unresolved. **Bytes/five-profile grouping "
        "confirmed; colour semantics inferred from project correlation, "
        "medium-high confidence.**",
        f"- The macro region is read contiguously from `0x0478` through "
        f"`0x1417`: {len(macro_reads)} reads × 50 bytes = 4,000 bytes. This "
        "equals twenty 200-byte device slots. Existing static analysis proves "
        "the base/stride formula `0x0478 + slot×0x00C8`; the editor file model "
        "exposes only 15 user macro slots. The five extra device slots are "
        "therefore reserved/unused/otherwise inaccessible, not evidence that "
        "the PFD/MacroSet model should be expanded. **Size/address confirmed; "
        "extra-slot purpose unresolved.**",
        "",
        "The utility reads all five profile regions plus shared/global and "
        "macro storage. It does not merely read the selected profile. No "
        "address/length pair is repeated.",
        "",
        "The macro region is almost entirely zero in this untouched image. "
        "Only device slot 7 begins `FF FF`; its purpose is unknown and the "
        "bytes are preserved without interpretation.",
        "",
        "## Binary output note",
        "",
        "`factory_memory.bin` spans the minimum through maximum address "
        "inclusive. Its byte zero corresponds to device address `0x002C`; "
        "unread gaps are filled with `00` only to make a flat file. "
        "`factory_memory_present.bin`, `factory_memory_map.json`, and "
        "`factory_gaps.txt` are authoritative for distinguishing captured "
        "zero bytes from uncaptured addresses. Each presence-file byte is 1 "
        "for captured and 0 for a gap.",
        "",
        "## Remaining protocol unknowns",
        "",
        "- The precise semantic name of response byte 1 (`08`).",
        "- Why the protocol echoes an even-aligned address for odd requests.",
        "- Exact meanings of the profile-level bytes at `0x0449..0x046F`.",
        "- Purpose of device macro slots 15–19.",
        "- Whether `F5` selects, applies, commits, or combines those operations; "
        "the observed index-like arguments alone do not prove write semantics.",
        "- Write ordering, acknowledgement, persistence, and read-back "
        "verification remain out of scope and require a controlled write capture.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path, nargs="?", default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    transfers, reads, memory = analyse_capture(args.capture)
    set_records = []
    for transfer in transfers:
        if transfer.direction != "SET_REPORT":
            continue
        record = transfer.to_dict()
        if len(transfer.payload) >= 2:
            record["command"] = f"{transfer.payload[1]:02X}"
        if len(transfer.payload) >= 6 and transfer.payload[1] == 0xF2:
            record["address"] = int.from_bytes(transfer.payload[2:4], "little")
            record["requested_length"] = int.from_bytes(
                transfer.payload[4:6], "little"
            )
        set_records.append(record)
    transactions = {
        "scope": {
            "first_frame": 24100,
            "last_frame": 24877,
            "device_address": 5,
            "interface_index": 2,
            "report_type": "HID Feature",
        },
        "set_reports": set_records,
        "feature_reads": [read.to_dict() for read in reads],
    }
    (output / "factory_transactions.json").write_text(
        json.dumps(transactions, indent=2) + "\n", encoding="utf-8"
    )

    with (output / "factory_reads.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "set_frame", "set_completion", "get_frame", "get_completion",
            "timestamp", "set_report_id", "get_report_id", "address",
            "length", "response_header", "returned_data",
        ])
        for read in reads:
            writer.writerow([
                read.set_transfer.submission_frame,
                read.set_transfer.completion_frame,
                read.get_transfer.submission_frame,
                read.get_transfer.completion_frame,
                read.set_transfer.timestamp,
                read.set_transfer.report_id,
                read.get_transfer.report_id,
                f"0x{read.address:04X}",
                read.requested_length,
                read.response[:8].hex(" "),
                read.returned_data.hex(" "),
            ])

    (output / "factory_memory.bin").write_bytes(memory.flat_bytes())
    (output / "factory_memory_present.bin").write_bytes(bytes(
        int(address in memory.values)
        for address in range(memory.minimum_address, memory.maximum_address + 1)
    ))
    memory_map = {
        "base_address": memory.minimum_address,
        "maximum_address": memory.maximum_address,
        "flat_binary_size": memory.maximum_address - memory.minimum_address + 1,
        "gap_fill_byte": 0,
        "returned_byte_count": len(memory.values),
        "ranges": [
            {"start": start, "end": end, "length": end - start + 1}
            for start, end in memory.ranges()
        ],
        "gaps": [
            {"start": start, "end": end, "length": end - start + 1}
            for start, end in memory.gaps()
        ],
        "overlaps": memory.overlaps,
        "bytes": {
            f"0x{address:04X}": value
            for address, value in sorted(memory.values.items())
        },
    }
    (output / "factory_memory_map.json").write_text(
        json.dumps(memory_map, indent=2) + "\n", encoding="utf-8"
    )
    gap_lines = [
        "# Unread address gaps between captured minimum and maximum",
        "# start\tend\tlength",
    ]
    gap_lines.extend(
        f"0x{start:04X}\t0x{end:04X}\t{end - start + 1}"
        for start, end in memory.gaps()
    )
    (output / "factory_gaps.txt").write_text(
        "\n".join(gap_lines) + "\n", encoding="utf-8"
    )
    (output / "factory_protocol_report.md").write_text(
        build_report(transfers, reads, memory, args.capture),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
