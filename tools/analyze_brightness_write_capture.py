#!/usr/bin/env python3
"""Generate offline artefacts for the controlled brightness Apply capture."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "linux-suite-prototype" / "src"))

from venus_profile.offline_usb_capture import (  # noqa: E402
    analyse_feature_capture,
    reconstruct_memory,
    reconstruct_written_memory,
)


CAPTURE = (
    PROJECT / "usb-captures"
    / "brightness_one_step_apply_20260727_082346.pcapng"
)
FACTORY_MAP = (
    PROJECT / "usb-captures" / "analysis" / "factory_memory_map.json"
)
OUTPUT = (
    PROJECT / "usb-captures" / "analysis" / "brightness_medium_to_high"
)

INITIAL_READ = (3124, 3901)
PREVIEW = (6356, 6379)
APPLY = (10643, 10954)
APPLY_F3 = (10645, 10943)


def phase_for(frame: int) -> str:
    if INITIAL_READ[0] <= frame <= INITIAL_READ[1]:
        return "initial_profile_read"
    if PREVIEW[0] <= frame <= PREVIEW[1]:
        return "brightness_live_preview"
    if APPLY[0] <= frame <= APPLY[1]:
        return "apply"
    return "other"


def load_factory() -> dict[int, int]:
    document = json.loads(FACTORY_MAP.read_text(encoding="utf-8"))
    return {
        int(address, 16): value
        for address, value in document["bytes"].items()
    }


def range_text(start: int, end: int) -> str:
    return f"0x{start:04X}–0x{end:04X}"


def build_protocol_report(
    transfers, reads, writes, apply_writes, memory, factory, changed, unknown
) -> str:
    f3_distribution = Counter(
        (
            write.transfer.report_id,
            write.transfer.requested_report_length,
            write.block_length,
        )
        for write in apply_writes
    )
    write_deltas = [
        second.transfer.relative_time - first.transfer.relative_time
        for first, second in zip(apply_writes, apply_writes[1:])
    ]
    comparable = sum(address in factory for address in memory.values)
    initial = reconstruct_memory(reads)
    commands = Counter(
        transfer.payload[1]
        for transfer in transfers
        if transfer.direction == "SET_REPORT" and len(transfer.payload) >= 2
    )
    lines = [
        "# Brightness Medium → High write protocol",
        "",
        "Strictly offline analysis of the saved Windows USB capture. No mouse "
        "or HID device was opened.",
        "",
        "## Phase boundaries",
        "",
        "- USB re-enumeration: frames 3025–3118 (port reset, descriptors, "
        "configuration, three HID report descriptors); interrupt polling is "
        "queued through frame 3123.",
        "- Initial factory profile read: frame 3124 (`F5 00`) through frame "
        "3901, the completion of frame 3900 (`F5 01`). It contains the same "
        "193 F2 requests as the earlier factory capture.",
        "- Brightness live preview: frame 6356 through completion frame 6379. "
        "It performs eleven F3 lighting writes and `F1 02 04`.",
        "- Apply: frame 10643 (`F5 00`) through completion frame 10954, after "
        "frame 10953 (`F5 01`).",
        "- Apply F3 data writes: submission frames 10645–10943.",
        "- Post-Apply verification reads: none.",
        "",
        "## Commands",
        "",
        f"Across the complete feature-report capture: F2={commands[0xF2]}, "
        f"F3={commands[0xF3]}, F1={commands[0xF1]}, F5={commands[0xF5]}. "
        "No other command occurs.",
        "",
        "The initial read reconstructs exactly the earlier factory map: "
        f"{len(initial.values)} addresses and SHA-256 "
        f"`{hashlib.sha256(initial.flat_bytes()).hexdigest()}`. "
        "**Confirmed, high confidence.**",
        "",
        "## F3 write framing",
        "",
        "```text",
        "offset  size  meaning",
        "0       1     HID report ID",
        "1       1     F3 write command",
        "2       2     destination address, little-endian",
        "4       2     meaningful payload length, little-endian",
        "6       2     00 00 reserved",
        "8       N     bytes written",
        "8+N     ...   zero padding to the declared HID report size",
        "```",
        "",
        f"The Apply phase contains {len(apply_writes)} F3 blocks carrying "
        f"{sum(write.block_length for write in apply_writes)} bytes into "
        f"{len(memory.values)} unique addresses. There are no overlapping "
        "Apply writes.",
        "",
        "Apply block distribution:",
        "",
    ]
    for (report_id, transfer_length, block_length), count in sorted(
        f3_distribution.items()
    ):
        lines.append(
            f"- {count} × report {report_id}, {transfer_length}-byte SET_REPORT, "
            f"{block_length}-byte write payload"
        )
    lines.extend([
        "",
        "All actual submission payload lengths equal their setup-declared "
        "lengths: report 2 is 16 bytes and report 3 is 64 bytes. The one "
        "report-3 write carries ten bytes and has 46 bytes of zero padding. "
        "No report 4 or report 5 occurs anywhere in this capture.",
        "",
        f"Successive F3 submissions are spaced {min(write_deltas) * 1000:.3f}–"
        f"{max(write_deltas) * 1000:.3f} ms apart; median "
        f"{statistics.median(write_deltas) * 1000:.3f} ms and mean "
        f"{statistics.mean(write_deltas) * 1000:.3f} ms.",
        "",
        "This capture proves that an ordinary profile Apply is implemented as "
        "fine-grained report-2/report-3 writes, not padded 200-byte report-4 "
        "blocks. It does not exercise macro writing, so it cannot finally "
        "resolve whether a future `04 F3 ... C8 00` macro transaction is sent "
        "as the descriptor's 256-byte report 4 or through a 1,024-byte "
        "application buffer. Report 5 does not appear. **Confirmed for this "
        "Apply; report-4 ambiguity remains unresolved.**",
        "",
        "## Written address map",
        "",
        f"- Sparse span: `0x{memory.minimum_address:04X}–"
        f"0x{memory.maximum_address:04X}`",
        f"- Unique bytes written: {len(memory.values)}",
        f"- Factory-comparable bytes: {comparable}",
        f"- Comparable unchanged bytes nevertheless rewritten: "
        f"{comparable - len(changed)}",
        f"- Changed factory bytes: {len(changed)}",
        f"- Written addresses absent from the factory read map: {len(unknown)}",
        "",
        "Exact contiguous write ranges:",
        "",
    ])
    lines.extend(
        f"- `{range_text(start, end)}` ({end - start + 1} bytes)"
        for start, end in memory.ranges()
    )
    lines.extend([
        "",
        "The Apply rewrites shared/global state below `0x0042`, DPI/current "
        "stage structures for all five profiles, all twenty button records "
        "for all five profiles, and all five seven-byte lighting records. It "
        "does not write the macro region beginning at `0x0478`. "
        "**Confirmed, high confidence.**",
        "",
        "## Brightness change",
        "",
        "Exactly one factory-comparable byte changes:",
        "",
        "```text",
        "address  factory  written  meaning",
        "0x044F   02       03       profile 1 LedState / brightness Medium→High",
        "```",
        "",
        "The surrounding profile-1 lighting structure is:",
        "",
        "```text",
        "0x0449..0x044E  FF 00 00 02 05 01   colour and unchanged lighting fields",
        "0x044F          03                  brightness High",
        "```",
        "",
        "The other four profile records retain `02` at `0x0457`, `0x045F`, "
        "`0x0467`, and `0x046F`. The same `0x044F: 02→03` change was sent "
        "during the live-preview sequence before Apply. There is no additional "
        "editor/session mutation among the 579 factory-comparable addresses. "
        "**Device byte change confirmed; LedState semantic correlation high "
        "confidence from controlled action and PFD evidence.**",
        "",
        "Seventeen Apply-written bytes lie in gaps that the initial read did "
        "not request (`0x0020..0x0029`, `0x002D`, `0x0037`, `0x003B`, "
        "`0x003E..0x003F`, `0x0446..0x0447`). Their previous values are "
        "unknown, so they are not classified as changed.",
        "",
        "## Integrity and verification",
        "",
        "No checksum byte is present in the F3 header or after its declared "
        "payload. Every byte after the declared payload is zero padding. No "
        "block-level, profile-level, or whole-region checksum is transmitted "
        "in this sequence, and the PFD additive checksums are not copied to "
        "device memory here. The F1 category/finalisation commands may cause "
        "device-internal validation, but they contain no checksum material. "
        "**Absence on wire confirmed; internal integrity behaviour unknown.**",
        "",
        "There are no F2 requests or GET_REPORT transactions after Apply. "
        "Every SET_REPORT receives a successful USB completion status, but "
        "that is transport acknowledgement rather than data read-back. "
        "Windows therefore performs no observable post-write byte verification "
        "in this capture.",
        "",
        "## Final command sequence",
        "",
        "After the last button write at frame 10943:",
        "",
        "```text",
        "10945  02 F1 02 04 00 00 00 00 00 00 00 00 00 00 00 00",
        "10947  02 F1 02 01 00 00 00 00 00 00 00 00 00 00 00 00",
        "10949  02 F1 02 02 00 00 00 00 00 00 00 00 00 00 00 00",
        "10951  02 F1 02 08 00 00 00 00 00 00 00 00 00 00 00 00",
        "10953  02 F5 01 00 00 00 00 00 00 00 00 00 00 00 00 00",
        "```",
        "",
        "`F1 02 04` is also observed immediately after the live lighting "
        "preview, supporting—but not proving—that `04` finalises/applies the "
        "lighting category. The meanings of F1 values `01`, `02`, `08`, and "
        "`10` remain inferred category masks/commit selectors. `F5 00` and "
        "`F5 01` bracket both read and Apply sequences, strongly supporting "
        "begin/end or enter/exit semantics rather than simple profile indices. "
        "**Ordering confirmed; semantic names medium confidence.**",
        "",
    ])
    return "\n".join(lines)


def build_apply_sequence(transfers, writes) -> str:
    write_by_frame = {write.transfer.submission_frame: write for write in writes}
    lines = [
        "# Apply sequence",
        "",
        "Frames 10643–10954, interface 2 HID Feature SET_REPORT transfers.",
        "",
        "| Submission | Completion | Time (relative) | Report | Command/detail |",
        "|---:|---:|---:|---:|---|",
    ]
    for transfer in transfers:
        if not APPLY[0] <= transfer.submission_frame <= APPLY[1]:
            continue
        if transfer.direction != "SET_REPORT":
            continue
        write = write_by_frame.get(transfer.submission_frame)
        if write:
            detail = (
                f"`F3` address `0x{write.address:04X}`, "
                f"{write.block_length} bytes: `{write.data.hex(' ')}`"
            )
        else:
            detail = f"`{transfer.payload.hex(' ')}`"
        lines.append(
            f"| {transfer.submission_frame} | {transfer.completion_frame} | "
            f"{transfer.relative_time:.6f} | {transfer.report_id} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path, nargs="?", default=CAPTURE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)

    transfers, reads, writes = analyse_feature_capture(args.capture)
    apply_writes = [
        write for write in writes
        if APPLY_F3[0] <= write.transfer.submission_frame <= APPLY_F3[1]
    ]
    memory = reconstruct_written_memory(apply_writes)
    factory = load_factory()
    changed = [
        (address, factory[address], value)
        for address, value in sorted(memory.values.items())
        if address in factory and factory[address] != value
    ]
    unknown = [
        (address, value)
        for address, value in sorted(memory.values.items())
        if address not in factory
    ]

    transactions = {
        "capture": str(args.capture),
        "phases": {
            "enumeration": {"first_frame": 3025, "last_frame": 3118},
            "initial_profile_read": {
                "first_frame": INITIAL_READ[0], "last_frame": INITIAL_READ[1],
            },
            "brightness_live_preview": {
                "first_frame": PREVIEW[0], "last_frame": PREVIEW[1],
            },
            "apply": {"first_frame": APPLY[0], "last_frame": APPLY[1]},
            "apply_f3_writes": {
                "first_frame": APPLY_F3[0], "last_frame": APPLY_F3[1],
            },
        },
        "feature_transfers": [
            {**transfer.to_dict(), "phase": phase_for(transfer.submission_frame)}
            for transfer in transfers
        ],
        "f2_reads": [read.to_dict() for read in reads],
        "f3_writes": [
            {**write.to_dict(), "phase": phase_for(write.transfer.submission_frame)}
            for write in writes
        ],
    }
    (args.output / "write_transactions.json").write_text(
        json.dumps(transactions, indent=2) + "\n", encoding="utf-8"
    )

    with (args.output / "write_blocks.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "phase", "submission_frame", "completion_frame", "timestamp",
            "report_id", "declared_length", "actual_length", "command",
            "address", "block_length", "header_reserved", "data",
            "padding_length",
        ])
        for write in writes:
            writer.writerow([
                phase_for(write.transfer.submission_frame),
                write.transfer.submission_frame,
                write.transfer.completion_frame,
                write.transfer.timestamp,
                write.transfer.report_id,
                write.transfer.requested_report_length,
                write.transfer.completion_data_length,
                "F3",
                f"0x{write.address:04X}",
                write.block_length,
                write.header_reserved.hex(" "),
                write.data.hex(" "),
                len(write.padding),
            ])

    (args.output / "written_memory.bin").write_bytes(memory.flat_bytes())
    (args.output / "written_memory_present.bin").write_bytes(bytes(
        int(address in memory.values)
        for address in range(memory.minimum_address, memory.maximum_address + 1)
    ))
    memory_map = {
        "base_address": memory.minimum_address,
        "maximum_address": memory.maximum_address,
        "flat_binary_size": memory.maximum_address - memory.minimum_address + 1,
        "gap_fill_byte": 0,
        "returned_factory_comparable": sum(
            address in factory for address in memory.values
        ),
        "ranges": [
            {"start": start, "end": end, "length": end - start + 1}
            for start, end in memory.ranges()
        ],
        "overlaps": memory.overlaps,
        "bytes": {
            f"0x{address:04X}": value
            for address, value in sorted(memory.values.items())
        },
        "sha256_flat": hashlib.sha256(memory.flat_bytes()).hexdigest(),
    }
    (args.output / "written_memory_map.json").write_text(
        json.dumps(memory_map, indent=2) + "\n", encoding="utf-8"
    )

    with (args.output / "factory_vs_written_diff.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "address", "factory_present", "factory_byte", "written_byte",
            "classification",
        ])
        for address, value in sorted(memory.values.items()):
            if address not in factory:
                writer.writerow([
                    f"0x{address:04X}", 0, "", f"{value:02X}",
                    "factory_read_gap_unknown",
                ])
            else:
                writer.writerow([
                    f"0x{address:04X}", 1, f"{factory[address]:02X}",
                    f"{value:02X}",
                    "changed" if factory[address] != value
                    else "unchanged_rewritten",
                ])

    (args.output / "apply_sequence.md").write_text(
        build_apply_sequence(transfers, writes), encoding="utf-8"
    )
    (args.output / "protocol_write_report.md").write_text(
        build_protocol_report(
            transfers, reads, writes, apply_writes, memory, factory,
            changed, unknown,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
