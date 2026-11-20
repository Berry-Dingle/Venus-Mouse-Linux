#!/usr/bin/env python3
"""Generate strictly offline artefacts for the controlled macro capture."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "linux-suite-prototype" / "src"))

from venus_profile.offline_usb_capture import (  # noqa: E402
    DeviceMacroAssignment,
    analyse_feature_capture,
    decode_device_macro_slot,
    reconstruct_memory,
    reconstruct_written_memory,
)


CAPTURE = (
    PROJECT / "usb-captures"
    / "macro_slot1_a_press_release_20260727_090056.pcapng"
)
FACTORY_MAP = (
    PROJECT / "usb-captures" / "analysis" / "factory_memory_map.json"
)
BRIGHTNESS_MAP = (
    PROJECT / "usb-captures" / "analysis" / "brightness_medium_to_high"
    / "written_memory_map.json"
)
OUTPUT = (
    PROJECT / "usb-captures" / "analysis"
    / "macro_slot1_a_press_release"
)

CYCLES = (
    ("cycle_1_initial_read", 2836, 3613),
    ("cycle_2_macro_upload", 4258, 4263),
    ("cycle_3_final_apply", 4370, 4681),
)
FINAL_APPLY_F3 = (4372, 4670)
MACRO_FRAME = 4260
BUTTON_ADDRESS = 0x009A


def phase_for(frame: int) -> str:
    for name, first, last in CYCLES:
        if first <= frame <= last:
            return name
    return "other"


def load_map(path: Path) -> dict[int, int]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(address, 16): value
        for address, value in document["bytes"].items()
    }


def build_report(
    transfers, reads, writes, macro_write, slot, apply_writes, apply_memory,
    factory, brightness,
) -> str:
    commands = Counter(
        transfer.payload[1]
        for transfer in transfers
        if transfer.direction == "SET_REPORT" and len(transfer.payload) >= 2
    )
    initial = reconstruct_memory(reads)
    assignment_raw = bytes(
        apply_memory.values[BUTTON_ADDRESS + offset] for offset in range(4)
    )
    assignment = DeviceMacroAssignment(assignment_raw)
    macro_changes = [
        (macro_write.address + offset, factory[macro_write.address + offset], value)
        for offset, value in enumerate(slot.raw)
        if factory[macro_write.address + offset] != value
    ]
    apply_factory_changes = [
        (address, factory[address], value)
        for address, value in sorted(apply_memory.values.items())
        if address in factory and factory[address] != value
    ]
    versus_brightness = [
        (address, brightness.get(address), value)
        for address, value in sorted(apply_memory.values.items())
        if brightness.get(address) != value
    ]
    lines = [
        "# Controlled macro-write protocol",
        "",
        "Strictly offline analysis. No HID device or live write interface was "
        "opened.",
        "",
        "## Restart/open cycles",
        "",
        "Three F5-bracketed application cycles are present:",
        "",
        "1. Frames 2836–3613: `F5 00`, 193 F2 factory/profile/macro reads, "
        "then `F5 01`.",
        "2. Frames 4258–4263: `F5 00`, one report-4 macro-slot F3 write at "
        "frame 4260, then `F5 01`.",
        "3. Frames 4370–4681: final Apply, bracketed by `F5 00`/`F5 01`.",
        "",
        "USB enumeration begins with the port reset at frame 2737; descriptor "
        "and HID-interface setup completes at frame 2830, with interrupt "
        "polling queued through frame 2835. Only the first application cycle "
        "does the complete read. The F5 brackets—not repeated USB enumeration—"
        "separate the three Windows utility runs. **Confirmed, high confidence.**",
        "",
        "Feature SET command totals: "
        f"F2={commands[0xF2]}, F3={commands[0xF3]}, "
        f"F1={commands[0xF1]}, F5={commands[0xF5]}.",
        "",
        "The initial read differs from the untouched factory image only at "
        "`0x044F`, which is already `03` because the preceding controlled "
        "brightness capture changed Medium to High. **Confirmed, high confidence.**",
        "",
        "## Macro transport",
        "",
        "Frame 4260 is one HID Feature SET_REPORT:",
        "",
        "```text",
        "report ID             4",
        "setup-declared size   256 bytes",
        "actual payload size   256 bytes",
        "header                04 F3 78 04 C8 00 00 00",
        "destination           0x0478",
        "declared slot bytes   0x00C8 = 200",
        "report padding        48 zero bytes",
        "completion            frame 4261, USB status 0",
        "```",
        "",
        "The transfer writes only `0x0478–0x053F`, device slot index 0. This "
        "confirms that the UI's “slot 1” is zero-based device slot 0. No other "
        "macro slot is written or rewritten. Because there is only one macro "
        "block, inter-block timing cannot be measured; it begins 117.652 ms "
        "after `F5 00` and the closing `F5 01` follows 186.718 ms later.",
        "",
        "This resolves the report-size discrepancy for macro writes: report 4 "
        "is 256 bytes on the wire, exactly matching the descriptor. The earlier "
        "1,024-byte disassembly object is an application-side allocation or "
        "maximum buffer, not the submitted transfer in this capture. Report 5 "
        "does not appear. **Confirmed, high confidence.**",
        "",
        "## Decoded device slot",
        "",
        f"`macro_slot1.bin` is exactly {len(slot.raw)} bytes; SHA-256 "
        f"`{hashlib.sha256(slot.raw).hexdigest()}`.",
        "",
        "```text",
        "record  offset  bytes       decode",
        "0       0       84 04 00    keyboard A down",
        "1       3       06 01 00    explicit 10 ms action delay",
        "2       6       04 04 00    keyboard A up",
        "3..62           00 00 00    zero action/padding records",
        "189..199        00 ... 00   eleven zero trailing slot bytes",
        "```",
        "",
        "The captured compiler output therefore contains three populated "
        "records, not the expected two: despite the UI's no-delay selection, "
        "it inserts `06 01 00` between key down and key up. This agrees with "
        "the earlier controlled no-delay files, where the editor retained "
        "minimum 10 ms delays. **Captured fact, high confidence; discrepancy "
        "with intended UI behavior explicitly noted.**",
        "",
        f"The additive checksum of bytes 0–188 is `0x"
        f"{slot.file_style_additive_checksum:02X}`, but device-slot byte 189 is "
        f"`0x{slot.byte_189:02X}`. Thus the file-format additive checksum is "
        "not embedded in the compiled 200-byte device slot. There is no "
        "device-slot header or non-zero footer inside those 200 bytes; the F3 "
        "header is outside them. **Confirmed, high confidence.**",
        "",
        "Against the factory slot, exactly six addresses change:",
        "",
    ]
    lines.extend(
        f"- `0x{address:04X}`: `{old:02X} → {new:02X}`"
        for address, old, new in macro_changes
    )
    lines.extend([
        "",
        "The remaining 194 slot bytes are rewritten as their factory value "
        "`00`.",
        "",
        "## Run mode and side-button assignment",
        "",
        "Side button 1 / `ButtonAssigned_7` maps to the seventh four-byte "
        "record in profile 1, device address `0x009A`. The Apply writes:",
        "",
        f"```text\n0x009A..0x009D  {assignment_raw.hex(' ').upper()}\n```",
        "",
        "Factory and prior brightness-Apply value:",
        "",
        "```text",
        "90 00 1E 00",
        "```",
        "",
        "Changed bytes:",
        "",
        "- `0x009A`: `90 → 91` — compiled macro function code.",
        "- `0x009C`: `1E → 01` — execution-count low byte.",
        "",
        f"Disassembly plus capture decode: function=`0x"
        f"{assignment.function_code:02X}`, slot/flags=`0x"
        f"{assignment.slot_and_flags:02X}` (slot index "
        f"{assignment.slot_index}, no while-held/toggle flag), execution "
        f"count={assignment.execution_count}. This represents device slot 0 "
        "with no repeat-while-held or toggle flags and an encoded execution "
        "count of 1: the editor's default single-execution behaviour. The "
        "slot reference is byte `0x009B=00`; "
        "`0x009C=01` is the repetition count, not the slot number. "
        "**Confirmed by capture and prior disassembly, high confidence.**",
        "",
        "The macro name `CaptureTest` is not transmitted to the device. The "
        "observation that names remain visible when only `MacroSet.MSDB` is "
        "left in place is consistent with MacroSet being the manager slot/name "
        "index and `.MSMACRO` files holding bodies. This capture independently "
        "shows that device writes contain compiled actions and numeric slot "
        "metadata only; it does not by itself prove every local-file role.",
        "",
        "## Generic Apply comparison",
        "",
        f"The final Apply has {len(apply_writes)} F3 writes and "
        f"{len(apply_memory.values)} unique written bytes, exactly the same "
        "address set and block-size pattern as the brightness Apply. Compared "
        "with that prior Apply, only two bytes differ:",
        "",
    ])
    lines.extend(
        f"- `0x{address:04X}`: `{old:02X} → {new:02X}`"
        for address, old, new in versus_brightness
    )
    lines.extend([
        "",
        "Compared directly with untouched factory memory, the final Apply also "
        "contains `0x044F: 02→03`; that is retained brightness state from the "
        "previous controlled test, not macro-session noise. Factory-comparable "
        "Apply changes are:",
        "",
    ])
    lines.extend(
        f"- `0x{address:04X}`: `{old:02X} → {new:02X}`"
        for address, old, new in apply_factory_changes
    )
    lines.extend([
        "",
        "No unrelated comparable byte differs from the brightness Apply. "
        "Seventeen generic writes still target factory-read gaps, exactly as "
        "in the brightness capture, and remain unclassifiable against factory.",
        "",
        "## Finalisation",
        "",
        "The final sequence after the last profile write is unchanged from the "
        "brightness Apply:",
        "",
        "```text",
        "4672  02 F1 02 04 00 00 00 00 00 00 00 00 00 00 00 00",
        "4674  02 F1 02 01 00 00 00 00 00 00 00 00 00 00 00 00",
        "4676  02 F1 02 02 00 00 00 00 00 00 00 00 00 00 00 00",
        "4678  02 F1 02 08 00 00 00 00 00 00 00 00 00 00 00 00",
        "4680  02 F5 01 00 00 00 00 00 00 00 00 00 00 00 00 00",
        "```",
        "",
        "Macro creation adds no new F1 selector. Its body upload is separately "
        "bracketed by F5 commands. There is no post-write GET_REPORT/read-back "
        "verification in either the macro-upload or final-Apply cycle; only "
        "successful USB completion status is observed.",
        "",
    ])
    return "\n".join(lines)


def build_sequence(transfers, writes) -> str:
    by_frame = {write.transfer.submission_frame: write for write in writes}
    lines = [
        "# Macro upload and final Apply sequence",
        "",
        "| Phase | Submit | Complete | Relative time | Report | Detail |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for transfer in transfers:
        if phase_for(transfer.submission_frame) not in {
            "cycle_2_macro_upload", "cycle_3_final_apply"
        } or transfer.direction != "SET_REPORT":
            continue
        write = by_frame.get(transfer.submission_frame)
        detail = (
            f"F3 `0x{write.address:04X}` +{write.block_length}: "
            f"`{write.data.hex(' ')}`"
            if write else f"`{transfer.payload.hex(' ')}`"
        )
        lines.append(
            f"| {phase_for(transfer.submission_frame)} | "
            f"{transfer.submission_frame} | {transfer.completion_frame} | "
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
    macro_writes = [write for write in writes if write.address >= 0x0478]
    if len(macro_writes) != 1:
        raise RuntimeError(f"expected one macro write, found {len(macro_writes)}")
    macro_write = macro_writes[0]
    slot = decode_device_macro_slot(
        macro_write.data, address=macro_write.address
    )
    apply_writes = [
        write for write in writes
        if FINAL_APPLY_F3[0] <= write.transfer.submission_frame
        <= FINAL_APPLY_F3[1]
    ]
    apply_memory = reconstruct_written_memory(apply_writes)
    factory = load_map(FACTORY_MAP)
    brightness = load_map(BRIGHTNESS_MAP)
    assignment = DeviceMacroAssignment(bytes(
        apply_memory.values[BUTTON_ADDRESS + offset] for offset in range(4)
    ))

    document = {
        "capture": str(args.capture),
        "cycles": [
            {"name": name, "first_frame": first, "last_frame": last}
            for name, first, last in CYCLES
        ],
        "enumeration": {"first_frame": 2737, "last_frame": 2830},
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
    (args.output / "macro_write_transactions.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )

    with (args.output / "macro_write_blocks.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "phase", "submit", "complete", "timestamp", "report_id",
            "declared_transfer_length", "actual_data_length", "address",
            "block_length", "data", "padding_length",
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
                f"0x{write.address:04X}",
                write.block_length,
                write.data.hex(" "),
                len(write.padding),
            ])

    (args.output / "macro_slot1.bin").write_bytes(slot.raw)
    decoded = {
        **slot.to_dict(),
        "ui_slot_number": 1,
        "device_slot_index": 0,
        "report": macro_write.to_dict(),
        "assignment": assignment.to_dict(),
        "controlled_name": "CaptureTest",
        "controlled_execution_behavior": (
            "default single execution; no repeat-while-held or toggle selected"
        ),
        "observed_delay_discrepancy": (
            "The compiled body contains 06 01 00 (10 ms) despite no-delay UI."
        ),
    }
    (args.output / "macro_slot1_decoded.json").write_text(
        json.dumps(decoded, indent=2) + "\n", encoding="utf-8"
    )

    with (args.output / "macro_factory_diff.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "address", "slot_offset", "factory", "written", "classification"
        ])
        for offset, value in enumerate(slot.raw):
            address = slot.address + offset
            old = factory[address]
            writer.writerow([
                f"0x{address:04X}", offset, f"{old:02X}", f"{value:02X}",
                "changed" if old != value else "unchanged_rewritten",
            ])

    with (args.output / "button1_assignment_diff.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "address", "field", "factory", "brightness_apply", "macro_apply",
            "classification",
        ])
        fields = [
            "function_code", "slot_and_loop_flags",
            "execution_count_low", "execution_count_high",
        ]
        for offset, field in enumerate(fields):
            address = BUTTON_ADDRESS + offset
            new = assignment.raw[offset]
            writer.writerow([
                f"0x{address:04X}", field, f"{factory[address]:02X}",
                f"{brightness[address]:02X}", f"{new:02X}",
                "changed_for_macro" if brightness[address] != new
                else "unchanged",
            ])

    (args.output / "macro_apply_sequence.md").write_text(
        build_sequence(transfers, writes), encoding="utf-8"
    )
    (args.output / "macro_write_protocol_report.md").write_text(
        build_report(
            transfers, reads, writes, macro_write, slot, apply_writes,
            apply_memory, factory, brightness,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
