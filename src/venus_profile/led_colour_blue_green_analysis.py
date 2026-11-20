"""Offline analysis of the controlled official-blue to official-green capture."""

from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from .led_colour_analysis import (
    BLUE_DEVICE,
    compare_live_reads,
)
from .led_mode_analysis import load_live_read
from .offline_usb_capture import (
    analyse_feature_capture,
    reconstruct_written_memory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_colour_blue_to_green_20260727_124731.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T114650.277357Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T115052.080309Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_blue_to_green"
PRIOR_ANALYSIS = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_red_to_blue" / "colour_transactions.json"

DEVICE_ADDRESS = 12
APPLICATION_SETUP = (1874, 1953)
INITIAL_READ = (1959, 2743)
PREVIEW = (2920, 2967)
PREVIEW_CYCLES = ((2920, 2943), (2944, 2967))
APPLY = (3001, 3312)
APPLY_F3 = (3003, 3301)
COLOUR_ADDRESS = 0x0449
GREEN_DEVICE = bytes.fromhex("00 ff 01")
GREEN_PREFIX = bytes.fromhex("00 ff 01 01 00 01")
TRANSIENT_PREVIEW_PREFIX = bytes.fromhex("02 b5 34 01 00 01")


def _phase(frame: int) -> str:
    if APPLICATION_SETUP[0] <= frame <= APPLICATION_SETUP[1]:
        return "application_usb_setup"
    if INITIAL_READ[0] <= frame <= INITIAL_READ[1]:
        return "initial_read"
    if PREVIEW[0] <= frame <= PREVIEW[1]:
        return "live_preview"
    if APPLY[0] <= frame <= APPLY[1]:
        return "apply"
    return "other"


def analyse_blue_to_green_capture(
    capture: Path = CAPTURE, *, pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    comparison = compare_live_reads(pre_directory, post_directory)
    transfers, reads, writes = analyse_feature_capture(
        capture, device_address=DEVICE_ADDRESS
    )
    preview_cycles = [
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
    prior_preview = [
        item for item in prior["f3_writes"]
        if item["phase"] == "live_preview"
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
    command_counts = Counter(
        item.payload[1] for item in transfers
        if item.direction == "SET_REPORT" and len(item.payload) >= 2
    )
    return {
        "capture": str(capture),
        "device_address": DEVICE_ADDRESS,
        "phases": {
            "application_usb_setup": {
                "first_frame": APPLICATION_SETUP[0],
                "last_frame": APPLICATION_SETUP[1],
            },
            "initial_read": {
                "first_frame": INITIAL_READ[0],
                "last_frame": INITIAL_READ[1],
            },
            "live_preview": {
                "first_frame": PREVIEW[0], "last_frame": PREVIEW[1],
                "cycles": [
                    {"first_frame": first, "last_frame": last}
                    for first, last in PREVIEW_CYCLES
                ],
            },
            "apply": {"first_frame": APPLY[0], "last_frame": APPLY[1]},
            "apply_f3": {
                "first_frame": APPLY_F3[0], "last_frame": APPLY_F3[1],
            },
        },
        "read_comparison": comparison,
        "colour_field": {
            "address": COLOUR_ADDRESS, "address_hex": "0x0449",
            "length": 3, "byte_order": "R G B",
            "blue_bytes": BLUE_DEVICE.hex(" "),
            "blue_rgb": list(BLUE_DEVICE),
            "green_bytes": GREEN_DEVICE.hex(" "),
            "green_rgb": list(GREEN_DEVICE),
            "green_prefix": GREEN_PREFIX.hex(" "),
            "direct_rgb_consistent_across_red_blue_green": True,
            "arbitrary_rgb_write_supported": False,
        },
        "counts": {
            "feature_transfers": len(transfers), "f2_reads": len(reads),
            "f3_writes": len(writes),
            "preview_f3_writes": sum(map(len, preview_cycles)),
            "preview_cycle_f3_writes": [len(items) for items in preview_cycles],
            "apply_f3_writes": len(apply),
            "apply_payload_bytes": sum(item.block_length for item in apply),
            "apply_unique_addresses": len(apply_memory.values),
            "commands": {
                f"{command:02X}": count
                for command, count in sorted(command_counts.items())
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
            "same_apply_address_length_plan_as_red_to_blue": [
                (item["address"], item["block_length"]) for item in prior_apply
            ] == [(item.address, item.block_length) for item in apply],
            "each_preview_cycle_matches_prior_narrow_plan": all(
                [(item.address, item.block_length) for item in cycle]
                == [(item["address"], item["block_length"])
                    for item in prior_preview]
                for cycle in preview_cycles
            ),
            "changed_from_immediate_pre_read": changed,
            "macro_region_touched": any(
                item.address + item.block_length > 0x0478 for item in apply
            ),
        },
    }


def _sequence(path: Path, title: str, records: list[dict[str, object]]) -> None:
    lines = [
        f"# {title}", "",
        "| Frame | Completion | Relative time | Report | Payload |",
        "|---:|---:|---:|---:|---|",
    ]
    for item in records:
        lines.append(
            f"| {item['submission_frame']} | {item['completion_frame']} | "
            f"{item['relative_time']:.6f} | {item['report_id']} | "
            f"`{item['payload']}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_analysis_outputs(
    output: Path = OUTPUT, *, analysis: dict[str, object] | None = None
) -> None:
    analysis = analysis or analyse_blue_to_green_capture()
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
        output / "preview_sequence.md", "Blue-to-green preview sequence",
        analysis["preview_commands"],
    )
    _sequence(
        output / "apply_sequence.md", "Blue-to-green Apply sequence",
        [
            item for item in analysis["feature_transfers"]
            if item["phase"] == "apply" and item["direction"] == "SET_REPORT"
        ],
    )
    writes = analysis["prefix_covering_writes"]
    report = f"""# Official Blue → Official Green protocol

Strictly offline analysis; no device discovery or live transport was used.

Both native reads contain 193 valid F2 reads, 4,604 returned bytes, and
identical coverage/presence maps. Exactly `0x0449–0x044B` changed:
`0E 0F FF` → `00 FF 01`. Mode/suffix `01 00 01` and brightness `03` did not
change. Official Green is therefore direct device RGB `(0,255,1)`, with full
prefix `00 FF 01 01 00 01`.

## Capture phases

- USB setup: frames 1874–1953.
- Initial read: frames 1959–2743.
- Preview cycle 1: frames 2920–2943.
- Preview cycle 2: frames 2944–2967.
- Apply: frames 3001–3312; F3 writes are frames 3003–3301.

Prefix-covering writes:

- Transient preview frame {writes[0]['submission_frame']}:
  `{writes[0]['complete_payload']}`.
- Selected-green preview frame {writes[1]['submission_frame']}:
  `{writes[1]['complete_payload']}`.
- Apply frame {writes[2]['submission_frame']}:
  `{writes[2]['complete_payload']}`.

The first preview value `02 B5 34` did not persist. The second preview and
Apply both contain exact Green `00 FF 01`. Each preview cycle uses the same
11-write/37-byte narrow lighting plan as the prior Red→Blue capture and ends
with exact `F1 02 04`; F5 is absent from previews. Apply uses the same
146-block/596-byte sparse plan, touches no macro address, and has no post-write
F2 verification.

The post-read proves the Windows-applied Green value persisted. Red, Blue, and
Green device fields are all direct RGB triples, but the known PFD red palette
conversion still differs. These palette samples do not establish safe
arbitrary-RGB writes, and no Green writer is implemented.
"""
    (output / "colour_protocol_report.md").write_text(
        report, encoding="utf-8"
    )
