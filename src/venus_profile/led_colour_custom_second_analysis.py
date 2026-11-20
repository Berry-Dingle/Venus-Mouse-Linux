"""Offline analysis of the second controlled custom-colour transition."""

from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from .led_colour_analysis import compare_live_reads
from .led_colour_custom_analysis import _sequence
from .led_mode_analysis import load_live_read
from .offline_usb_capture import analyse_feature_capture, reconstruct_written_memory


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_colour_custom_7B2DD2_to_12A7E4_20260727_134011.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T123947.908575Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T124428.835671Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_custom_7B2DD2_to_12A7E4"
PRIOR_ANALYSIS = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_green_to_custom_7B2DD2" / "colour_transactions.json"

DEVICE_ADDRESS = 14
USB_SETUP = (1148, 1227)
INITIAL_READ = (1233, 2010)
PREVIEW = (2059, 2284)
PREVIEW_CYCLES = (
    (2059, 2082), (2087, 2110), (2117, 2140), (2143, 2166),
    (2171, 2194), (2207, 2232), (2233, 2256), (2261, 2284),
)
APPLY = (3484, 3795)
APPLY_F3 = (3486, 3784)
COLOUR_ADDRESS = 0x0449
SOURCE_PREFIX = bytes.fromhex("d2 2d 7b 01 00 01")
TARGET_PREFIX = bytes.fromhex("e4 a7 12 01 00 01")
SEMANTIC_RGB = (18, 167, 228)


def _phase(frame: int) -> str:
    if USB_SETUP[0] <= frame <= USB_SETUP[1]:
        return "usb_setup"
    if INITIAL_READ[0] <= frame <= INITIAL_READ[1]:
        return "initial_read"
    if PREVIEW[0] <= frame <= PREVIEW[1]:
        return "custom_picker_preview"
    if APPLY[0] <= frame <= APPLY[1]:
        return "apply"
    return "other"


def analyse_second_custom_colour_capture(
    capture: Path = CAPTURE, *, pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    comparison = compare_live_reads(pre_directory, post_directory)
    transfers, reads, writes = analyse_feature_capture(
        capture, device_address=DEVICE_ADDRESS
    )
    cycles = [
        [
            item for item in writes
            if first <= item.transfer.submission_frame <= last
        ]
        for first, last in PREVIEW_CYCLES
    ]
    apply = [
        item for item in writes
        if APPLY_F3[0] <= item.transfer.submission_frame <= APPLY_F3[1]
    ]
    prefix_writes = [
        item for item in writes
        if item.address <= COLOUR_ADDRESS
        and item.address + item.block_length >= COLOUR_ADDRESS + 6
    ]
    apply_memory = reconstruct_written_memory(apply)
    pre_memory = load_live_read(pre_directory)["memory"]
    changed = [
        {
            "address": address, "address_hex": f"0x{address:04X}",
            "previous": pre_memory[address], "written": value,
        }
        for address, value in sorted(apply_memory.values.items())
        if address in pre_memory and pre_memory[address] != value
    ]
    prior = json.loads(PRIOR_ANALYSIS.read_text(encoding="utf-8"))
    prior_apply = [
        item for item in prior["f3_writes"] if item["phase"] == "apply"
    ]
    prior_cycle_plan = [
        (item["address"], item["block_length"])
        for item in prior["f3_writes"]
        if 2119 <= item["submission_frame"] <= 2139
    ]
    final_commands = [
        item.to_dict() for item in transfers
        if item.direction == "SET_REPORT"
        and APPLY[0] <= item.submission_frame <= APPLY[1]
        and len(item.payload) >= 2 and item.payload[1] in {0xF1, 0xF5}
    ]
    preview_commands = [
        item.to_dict() for item in transfers
        if item.direction == "SET_REPORT"
        and PREVIEW[0] <= item.submission_frame <= PREVIEW[1]
    ]
    counts = Counter(
        item.payload[1] for item in transfers
        if item.direction == "SET_REPORT" and len(item.payload) >= 2
    )
    return {
        "capture": str(capture), "device_address": DEVICE_ADDRESS,
        "phases": {
            "usb_setup": {"first_frame": 1148, "last_frame": 1227},
            "initial_read": {"first_frame": 1233, "last_frame": 2010},
            "custom_picker_preview": {
                "first_frame": 2059, "last_frame": 2284,
                "cycles": [
                    {"first_frame": first, "last_frame": last}
                    for first, last in PREVIEW_CYCLES
                ],
            },
            "apply": {"first_frame": 3484, "last_frame": 3795},
            "apply_f3": {"first_frame": 3486, "last_frame": 3784},
        },
        "read_comparison": comparison,
        "colour_field": {
            "address": COLOUR_ADDRESS, "address_hex": "0x0449", "length": 3,
            "source_custom_hex": "#7B2DD2",
            "source_device_bytes": SOURCE_PREFIX[:3].hex(" "),
            "target_custom_hex": "#12A7E4",
            "target_semantic_rgb": list(SEMANTIC_RGB),
            "target_device_bytes": TARGET_PREFIX[:3].hex(" "),
            "target_prefix": TARGET_PREFIX.hex(" "),
            "windows_picker_channel_transform": "entered RRGGBB -> written BBGGRR",
            "native_device_channel_order": "R G B",
            "visible_native_rgb": [228, 167, 18],
            "later_live_test_disproved_native_bgr_rule": True,
            "arbitrary_24bit_writer_evidence_sufficient": True,
            "live_arbitrary_writer_enabled": False,
        },
        "counts": {
            "feature_transfers": len(transfers), "f2_reads": len(reads),
            "f3_writes": len(writes),
            "preview_f3_writes": sum(map(len, cycles)),
            "preview_cycle_f3_writes": [len(items) for items in cycles],
            "apply_f3_writes": len(apply),
            "apply_payload_bytes": sum(item.block_length for item in apply),
            "apply_unique_addresses": len(apply_memory.values),
            "commands": {
                f"{key:02X}": value for key, value in sorted(counts.items())
            },
        },
        "feature_transfers": [
            {**item.to_dict(), "phase": _phase(item.submission_frame)}
            for item in transfers
        ],
        "f3_writes": [
            {**item.to_dict(), "phase": _phase(item.transfer.submission_frame)}
            for item in writes
        ],
        "prefix_covering_writes": [item.to_dict() for item in prefix_writes],
        "preview_commands": preview_commands,
        "final_commands": final_commands,
        "apply_comparison": {
            "same_146_block_596_byte_plan": (
                len(apply) == 146
                and sum(item.block_length for item in apply) == 596
                and len(apply_memory.values) == 596
            ),
            "same_apply_address_length_plan_as_first_custom": [
                (item["address"], item["block_length"]) for item in prior_apply
            ] == [(item.address, item.block_length) for item in apply],
            "each_preview_cycle_matches_first_custom_narrow_plan": all(
                [(item.address, item.block_length) for item in cycle]
                == prior_cycle_plan for cycle in cycles
            ),
            "changed_from_immediate_pre_read": changed,
            "macro_region_touched": any(
                item.address + item.block_length > 0x0478 for item in apply
            ),
        },
    }


def write_analysis_outputs(
    output: Path = OUTPUT, *, analysis: dict[str, object] | None = None
) -> None:
    analysis = analysis or analyse_second_custom_colour_capture()
    output.mkdir(parents=True, exist_ok=True)
    (output / "colour_transactions.json").write_text(
        json.dumps(analysis, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "colour_write_blocks.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "phase", "submission_frame", "completion_frame", "timestamp",
            "report_id", "transfer_length", "address", "block_length",
            "data", "complete_payload",
        ])
        for item in analysis["f3_writes"]:
            writer.writerow([
                item["phase"], item["submission_frame"],
                item["completion_frame"], item["timestamp"],
                item["report_id"], item["declared_transfer_length"],
                item["address_hex"], item["block_length"], item["data"],
                item["complete_payload"],
            ])
    with (output / "pre_vs_post_diff.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "address", "previous", "new", "classification",
            "pre_surrounding", "post_surrounding",
        ])
        for item in analysis["read_comparison"]["changed_addresses"]:
            writer.writerow([
                item["address_hex"], item["previous_hex"], item["new_hex"],
                item["classification"], item["pre_surrounding"],
                item["post_surrounding"],
            ])
    _sequence(
        output / "preview_sequence.md",
        "Second custom-colour picker preview sequence",
        analysis["preview_commands"],
    )
    _sequence(
        output / "apply_sequence.md", "Second custom-colour Apply sequence",
        [
            item for item in analysis["feature_transfers"]
            if item["phase"] == "apply" and item["direction"] == "SET_REPORT"
        ],
    )
    writes = analysis["prefix_covering_writes"]
    intermediates = "\n".join(
        f"- Frame {item['submission_frame']}: `{item['complete_payload']}`"
        for item in writes[:-2]
    )
    report = f"""# Custom colour #7B2DD2 → #12A7E4 protocol

Strictly offline analysis; no discovery, hidraw access, or live transport.

Both native reads contain 193 valid F2 reads and 4,604 returned bytes with
identical coverage. Exactly `0x0449–0x044B` changed:
`D2 2D 7B` → `E4 A7 12`. Suffix `01 00 01` and brightness `03` did not
change. The complete persistent prefix is `E4 A7 12 01 00 01`.

The entered semantic colour `#12A7E4` is RGB `(18,167,228)`. Final preview
frame {writes[-2]['submission_frame']} and Apply frame
{writes[-1]['submission_frame']} both contain Windows-written bytes
`E4 A7 12`.

Intermediate picker prefix writes:
{intermediates}

Each of eight previews uses the same 11-write/37-byte narrow lighting plan
and ends with exact `F1 02 04`; preview has no F5. Apply uses the same
146-block/596-byte plan as the first custom capture, touches no macro address,
and contains no post-write F2 verification.

Together, the samples confirm that the **Windows custom picker** reversed its
entered channels before writing. **Erratum:** a later native visible-colour
experiment proved device memory itself is direct `RR GG BB`; raw `E4 A7 12`
is visible/native `#E4A712`, not `#12A7E4`. Native Linux must not reproduce
the Windows picker reversal. Original frames and bytes remain preserved.
"""
    (output / "custom_colour_protocol_report.md").write_text(
        report, encoding="utf-8"
    )


def main() -> int:
    write_analysis_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
