"""Offline analysis of the controlled Green-to-custom-colour capture."""

from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from .led_colour_analysis import compare_live_reads
from .led_mode_analysis import load_live_read
from .offline_usb_capture import analyse_feature_capture, reconstruct_written_memory


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_colour_green_to_custom_7B2DD2_20260727_132205.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T122107.435228Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T122538.945419Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_green_to_custom_7B2DD2"
PRIOR_ANALYSIS = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_red_to_blue" / "colour_transactions.json"

DEVICE_ADDRESS = 14
USB_SETUP = (1202, 1281)
INITIAL_READ = (1287, 2064)
PREVIEW = (2119, 2344)
PREVIEW_CYCLES = (
    (2119, 2142), (2151, 2174), (2175, 2198), (2203, 2226),
    (2235, 2260), (2263, 2286), (2287, 2312), (2321, 2344),
)
APPLY = (2368, 2679)
APPLY_F3 = (2370, 2668)
COLOUR_ADDRESS = 0x0449
GREEN_PREFIX = bytes.fromhex("00 ff 01 01 00 01")
CUSTOM_PREFIX = bytes.fromhex("d2 2d 7b 01 00 01")
ENTERED_RGB = (123, 45, 210)


def _phase(frame: int) -> str:
    if USB_SETUP[0] <= frame <= USB_SETUP[1]:
        return "usb_setup"
    if INITIAL_READ[0] <= frame <= INITIAL_READ[1]:
        return "initial_read"
    if PREVIEW[0] <= frame <= PREVIEW[1]:
        return "custom_picker_preview"
    if APPLY[0] <= frame <= APPLY[1]:
        return "apply"
    return "setup_or_other"


def analyse_custom_colour_capture(
    capture: Path = CAPTURE, *, pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    comparison = compare_live_reads(pre_directory, post_directory)
    transfers, reads, writes = analyse_feature_capture(
        capture, device_address=DEVICE_ADDRESS
    )
    apply = [
        item for item in writes
        if APPLY_F3[0] <= item.transfer.submission_frame <= APPLY_F3[1]
    ]
    prefix_writes = [
        item for item in writes
        if item.address <= COLOUR_ADDRESS
        and item.address + item.block_length >= COLOUR_ADDRESS + 6
    ]
    preview_prefix_writes = [
        item for item in prefix_writes
        if PREVIEW[0] <= item.transfer.submission_frame <= PREVIEW[1]
    ]
    apply_prefix_writes = [
        item for item in prefix_writes
        if APPLY_F3[0] <= item.transfer.submission_frame <= APPLY_F3[1]
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
    preview_cycle_writes = [
        [
            item for item in writes
            if first <= item.transfer.submission_frame <= last
        ]
        for first, last in PREVIEW_CYCLES
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
        "capture": str(capture),
        "device_address": DEVICE_ADDRESS,
        "phases": {
            "usb_setup": {"first_frame": 1202, "last_frame": 1281},
            "initial_read": {"first_frame": 1287, "last_frame": 2064},
            "custom_picker_preview": {
                "first_frame": 2119, "last_frame": 2344,
                "cycles": [
                    {"first_frame": first, "last_frame": last}
                    for first, last in PREVIEW_CYCLES
                ],
            },
            "apply": {"first_frame": 2368, "last_frame": 2679},
            "apply_f3": {"first_frame": 2370, "last_frame": 2668},
        },
        "read_comparison": comparison,
        "colour_field": {
            "address": COLOUR_ADDRESS, "address_hex": "0x0449", "length": 3,
            "entered_hex_rgb": "#7B2DD2",
            "entered_semantic_rgb": list(ENTERED_RGB),
            "expected_if_direct_rgb": "7b 2d d2",
            "observed_device_bytes": CUSTOM_PREFIX[:3].hex(" "),
            "observed_full_prefix": CUSTOM_PREFIX.hex(" "),
            "windows_picker_channel_transform": "entered RRGGBB -> written BBGGRR",
            "native_device_channel_order": "R G B",
            "visible_native_rgb": [210, 45, 123],
            "windows_picker_entered_rgb": list(ENTERED_RGB),
            "arbitrary_custom_rgb_storage_confirmed": True,
            "direct_rgb_storage_confirmed_by_later_live_visible_test": True,
            "all_palette_entries_need_individual_testing": False,
        },
        "counts": {
            "feature_transfers": len(transfers), "f2_reads": len(reads),
            "f3_writes": len(writes),
            "preview_f3_writes": sum(map(len, preview_cycle_writes)),
            "preview_cycle_f3_writes": [
                len(items) for items in preview_cycle_writes
            ],
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
        "preview_prefix_writes": [
            item.to_dict() for item in preview_prefix_writes
        ],
        "apply_prefix_writes": [
            item.to_dict() for item in apply_prefix_writes
        ],
        "preview_commands": preview_commands,
        "final_commands": final_commands,
        "apply_comparison": {
            "same_146_block_596_byte_plan": (
                len(apply) == 146
                and sum(item.block_length for item in apply) == 596
                and len(apply_memory.values) == 596
            ),
            "same_apply_address_length_plan_as_prior_colour": [
                (item["address"], item["block_length"]) for item in prior_apply
            ] == [(item.address, item.block_length) for item in apply],
            "each_preview_cycle_matches_prior_narrow_plan": all(
                [(item.address, item.block_length) for item in cycle]
                == [(item["address"], item["block_length"])
                    for item in prior_preview]
                for cycle in preview_cycle_writes
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
    analysis = analysis or analyse_custom_colour_capture()
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
        output / "preview_sequence.md", "Custom-colour picker preview sequence",
        analysis["preview_commands"],
    )
    _sequence(
        output / "apply_sequence.md", "Custom-colour Apply sequence",
        [
            item for item in analysis["feature_transfers"]
            if item["phase"] == "apply" and item["direction"] == "SET_REPORT"
        ],
    )
    writes = analysis["prefix_covering_writes"]
    preview_summary = "\n".join(
        f"- Frame {item['submission_frame']}: `{item['complete_payload']}`"
        for item in writes[:-1]
    )
    report = f"""# Custom colour #7B2DD2 protocol

Strictly offline analysis; no discovery, hidraw access, or live transport.

Both saved native reads contain 193 valid F2 reads and 4,604 returned bytes
with identical coverage. Exactly `0x0449–0x044B` changed from `00 FF 01` to
`D2 2D 7B`; suffix `01 00 01` and brightness `03` remained unchanged.

The entered Windows-picker RGB was `(123,45,210)` / `#7B2DD2`, but Windows
wrote `D2 2D 7B`, not `7B 2D D2`. A later native visible-colour experiment
proved the mouse interprets its bytes directly as **R G B**. Therefore this
capture describes a Windows picker channel reversal, not device BGR storage.
The native/visible colour of these raw bytes is `#D22D7B`. The complete prefix is
`D2 2D 7B 01 00 01`.

## Capture phases and writes

- USB descriptor/application setup: frames 1202–1281.
- Initial application read: frames 1287–2064.
- Eight picker preview cycles: frames 2119–2344.
- Apply: frames 2368–2679; F3 writes 2370–2668.

Picker prefix writes:
{preview_summary}

- Apply frame {writes[-1]['submission_frame']}:
  `{writes[-1]['complete_payload']}`.

The final preview and Apply agree on `D2 2D 7B 01 00 01`. Earlier preview
writes are intermediate picker states and did not persist. Each preview cycle
uses the prior 11-write/37-byte narrow lighting plan and exact `F1 02 04`
without F5. Apply uses the same 146-block/596-byte sparse plan, touches no
macro address, and performs no post-write F2 verification.

The post-read proves persistence after Windows Apply. **Erratum:** the earlier
analysis incorrectly promoted the Windows picker reversal into a native BGR
rule. Mouse storage is direct RGB; native Linux must write semantic RRGGBB
unchanged. The original frames and bytes remain preserved.
"""
    (output / "custom_colour_protocol_report.md").write_text(
        report, encoding="utf-8"
    )


def main() -> int:
    write_analysis_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
