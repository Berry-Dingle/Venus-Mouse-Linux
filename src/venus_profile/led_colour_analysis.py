"""Reusable offline analysis of the controlled Venus red-to-blue capture."""

from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from .led_mode_analysis import load_live_read
from .offline_usb_capture import (
    analyse_feature_capture,
    reconstruct_written_memory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_colour_red_to_blue_20260727_121654.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T111614.233675Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T112130.097148Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_colour_red_to_blue"
BRIGHTNESS_ANALYSIS = PROJECT_ROOT / "usb-captures" / "analysis" / "brightness_medium_to_high" / "write_transactions.json"
MODE_ANALYSIS = PROJECT_ROOT / "usb-captures" / "analysis" / "led_mode_breathing_to_full" / "led_mode_transactions.json"

DEVICE_ADDRESS = 10
APPLICATION_SETUP = (3747, 3832)
INITIAL_READ = (3838, 4615)
PREVIEW = (8206, 8229)
APPLY = (8242, 8553)
APPLY_F3 = (8244, 8543)
COLOUR_ADDRESS = 0x0449
COLOUR_LENGTH = 3
RED_DEVICE = bytes.fromhex("ff 00 00")
BLUE_DEVICE = bytes.fromhex("0e 0f ff")


class LedColourAnalysisError(RuntimeError):
    """Saved evidence is malformed or contradicts the controlled experiment."""


def compare_live_reads(
    pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    pre = load_live_read(pre_directory)
    post = load_live_read(post_directory)
    if pre["coverage"] != post["coverage"]:
        raise LedColourAnalysisError("pre/post address coverage differs")
    if pre["present"] != post["present"]:
        raise LedColourAnalysisError("pre/post presence maps differ")
    changes = []
    for address in pre["coverage"]:
        before = pre["memory"][address]
        after = post["memory"][address]
        if before == after:
            continue
        start, end = address - 6, address + 6
        changes.append({
            "address": address,
            "address_hex": f"0x{address:04X}",
            "previous": before,
            "previous_hex": f"{before:02X}",
            "new": after,
            "new_hex": f"{after:02X}",
            "pre_surrounding": bytes(
                pre["memory"].get(item, 0) for item in range(start, end + 1)
            ).hex(" "),
            "post_surrounding": bytes(
                post["memory"].get(item, 0) for item in range(start, end + 1)
            ).hex(" "),
            "classification": "profile-1-led-colour-rgb-byte",
            "previous_mapping_status": "previously-unresolved-lighting-prefix",
        })
    return {
        "pre": {
            "directory": pre["directory"], "device": pre["device"],
            "read_count": pre["read_count"],
            "returned_byte_count": pre["returned_byte_count"],
            "presence_sha256": pre["presence_sha256"],
        },
        "post": {
            "directory": post["directory"], "device": post["device"],
            "read_count": post["read_count"],
            "returned_byte_count": post["returned_byte_count"],
            "presence_sha256": post["presence_sha256"],
        },
        "identical_coverage": True,
        "identical_presence_map": True,
        "changed_addresses": changes,
        "pre_colour": bytes(
            pre["memory"][COLOUR_ADDRESS + offset]
            for offset in range(COLOUR_LENGTH)
        ).hex(" "),
        "post_colour": bytes(
            post["memory"][COLOUR_ADDRESS + offset]
            for offset in range(COLOUR_LENGTH)
        ).hex(" "),
    }


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


def _apply_records(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return [
        item for item in document["f3_writes"]
        if item.get("phase") == "apply"
    ]


def analyse_led_colour_capture(
    capture: Path = CAPTURE,
    *,
    pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    comparison = compare_live_reads(pre_directory, post_directory)
    transfers, reads, writes = analyse_feature_capture(
        capture, device_address=DEVICE_ADDRESS
    )
    preview = [
        item for item in writes
        if PREVIEW[0] <= item.transfer.submission_frame <= PREVIEW[1]
    ]
    apply = [
        item for item in writes
        if APPLY_F3[0] <= item.transfer.submission_frame <= APPLY_F3[1]
    ]
    colour_writes = [
        item for item in writes
        if item.address <= COLOUR_ADDRESS
        and item.address + item.block_length >= COLOUR_ADDRESS + COLOUR_LENGTH
    ]
    apply_memory = reconstruct_written_memory(apply)
    pre_memory = load_live_read(pre_directory)["memory"]
    changed_from_pre = [
        {
            "address": address, "address_hex": f"0x{address:04X}",
            "previous": pre_memory[address], "written": value,
        }
        for address, value in sorted(apply_memory.values.items())
        if address in pre_memory and pre_memory[address] != value
    ]
    plan_comparisons = {}
    for name, path in (
        ("brightness", BRIGHTNESS_ANALYSIS), ("led_mode", MODE_ANALYSIS)
    ):
        older = _apply_records(path)
        plan_comparisons[name] = {
            "same_address_length_plan": [
                (item["address"], item["block_length"]) for item in older
            ] == [(item.address, item.block_length) for item in apply],
            "payload_differences": [
                {
                    "old_frame": old["submission_frame"],
                    "new_frame": new.transfer.submission_frame,
                    "address_hex": f"0x{new.address:04X}",
                    "old_data": old["data"],
                    "new_data": new.data.hex(" "),
                }
                for old, new in zip(older, apply)
                if bytes.fromhex(old["data"]) != new.data
            ],
        }
    commands = Counter(
        item.payload[1] for item in transfers
        if item.direction == "SET_REPORT" and len(item.payload) >= 2
    )
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
    return {
        "capture": str(capture),
        "device_address": DEVICE_ADDRESS,
        "phases": {
            "application_usb_setup": {
                "first_frame": APPLICATION_SETUP[0],
                "last_frame": APPLICATION_SETUP[1],
                "classification": "USB descriptor/setup traffic",
            },
            "initial_read": {
                "first_frame": INITIAL_READ[0],
                "last_frame": INITIAL_READ[1],
            },
            "live_preview": {
                "first_frame": PREVIEW[0], "last_frame": PREVIEW[1],
            },
            "apply": {"first_frame": APPLY[0], "last_frame": APPLY[1]},
            "apply_f3": {
                "first_frame": APPLY_F3[0], "last_frame": APPLY_F3[1],
            },
        },
        "read_comparison": comparison,
        "colour_field": {
            "address": COLOUR_ADDRESS, "address_hex": "0x0449",
            "length": COLOUR_LENGTH, "byte_order": "R G B",
            "red_device_bytes": RED_DEVICE.hex(" "),
            "red_device_rgb": [255, 0, 0],
            "blue_device_bytes": BLUE_DEVICE.hex(" "),
            "blue_device_rgb": [14, 15, 255],
            "pfd_red_colourref_bytes": "b9 00 01 00",
            "pfd_blue_colourref_bytes": "0e 0f ff 00",
            "pfd_stored_unchanged": False,
            "arbitrary_rgb_supported_by_evidence": False,
        },
        "counts": {
            "feature_transfers": len(transfers), "f2_reads": len(reads),
            "f3_writes": len(writes), "preview_f3_writes": len(preview),
            "apply_f3_writes": len(apply),
            "apply_payload_bytes": sum(item.block_length for item in apply),
            "apply_unique_addresses": len(apply_memory.values),
            "commands": {
                f"{command:02X}": count
                for command, count in sorted(commands.items())
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
        "colour_covering_writes": [item.to_dict() for item in colour_writes],
        "preview_commands": preview_commands,
        "final_commands": final_commands,
        "apply_comparison": {
            "same_146_block_596_byte_plan": (
                len(apply) == 146
                and sum(item.block_length for item in apply) == 596
                and len(apply_memory.values) == 596
            ),
            "prior_captures": plan_comparisons,
            "changed_from_immediate_pre_read": changed_from_pre,
            "macro_region_touched": any(
                item.address + item.block_length > 0x0478 for item in apply
            ),
        },
    }


def _write_sequence(path: Path, title: str, records: list[dict[str, object]]) -> None:
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
    analysis = analysis or analyse_led_colour_capture()
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
    _write_sequence(
        output / "preview_sequence.md", "LED-colour preview sequence",
        analysis["preview_commands"],
    )
    _write_sequence(
        output / "apply_sequence.md", "LED-colour Apply sequence",
        [
            item for item in analysis["feature_transfers"]
            if item["phase"] == "apply" and item["direction"] == "SET_REPORT"
        ],
    )
    writes = analysis["colour_covering_writes"]
    report = f"""# LED colour red → blue protocol

Strictly offline analysis; no device discovery or live transport was used.

## Native pre/post reads

Both reads identify `04D9:FC55`, interface `02`, usage `FFA0:0001`, with 193
valid F2 reads and 4,604 returned bytes. Coverage and presence maps are
identical. Exactly `0x0449–0x044B` changed: `FF 00 00` → `0E 0F FF`.

The three-byte device field is direct `R G B` order. The controlled red device
value is RGB `(255,0,0)` and blue is `(14,15,255)`. Blue matches the first
three little-endian PFD COLORREF bytes; red does not match the known PFD
palette bytes `B9 00 01 00`. The PFD four-byte value is therefore not stored
unchanged in device memory.

## Capture

- USB setup: frames 3747–3832.
- Initial read: frames 3838–4615.
- Preview: frames 8206–8229.
- Apply: frames 8242–8553; F3 writes are 8244–8543.
- Preview colour F3: frame {writes[0]['submission_frame']}:
  `{writes[0]['complete_payload']}`.
- Apply colour F3: frame {writes[1]['submission_frame']}:
  `{writes[1]['complete_payload']}`.
- Both contain colour bytes `0E 0F FF`.
- Preview ends with exact `F1 02 04` at frame 8228; F5 is absent.
- Apply begins with `F5 00` at frame 8242. After its final F3 it sends
  `F1 02 04` (8544), `F1 02 01` (8546), `F1 02 02` (8548),
  `F1 02 08` (8550), and `F5 01` (8552).
- No post-Apply F2 verification occurs.

Apply uses the same 146-block, 596-byte sparse address plan as the prior
brightness and LED-mode captures. Relative to the immediate pre-read, only
the three colour bytes changed. Against the immediately preceding LED-mode
capture, the colour prefix is the sole Apply payload difference. Differences
against the older brightness capture also reflect the later established mode
and button-1 macro state, not colour-session noise. The macro region is
untouched.

## Candidate guarded experiment

The smallest captured preview is eleven lighting F3 writes followed by exact
`F1 02 04`; F5 is absent. A narrowed candidate is an F2 pre-read of the
six-byte prefix at `0x0449`, exact six-byte F3
`02 F3 49 04 06 00 00 00 0E 0F FF 01 00 01 00 00`, a conservative 10 ms
delay, exact `F1 02 04`, another 10 ms delay, and six-byte F2 read-back.
That reduced sequence is not yet proven persistent.

Only the two controlled palette outcomes are proven. The red transformation
means arbitrary RGB device writes cannot safely be inferred from PFD COLORREF
semantics.
"""
    (output / "colour_protocol_report.md").write_text(
        report, encoding="utf-8"
    )
