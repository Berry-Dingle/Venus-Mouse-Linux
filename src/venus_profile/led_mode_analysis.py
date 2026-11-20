"""Reusable offline analysis for the controlled Venus LED-mode capture."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics

from .offline_usb_capture import (
    analyse_feature_capture,
    reconstruct_written_memory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = (
    PROJECT_ROOT / "usb-captures"
    / "led_mode_breathing_to_full_20260727_111221.pcapng"
)
PRE_READ = (
    PROJECT_ROOT / "usb-captures" / "live-reads"
    / "venus_read_20260727T101006.724423Z"
)
POST_READ = (
    PROJECT_ROOT / "usb-captures" / "live-reads"
    / "venus_read_20260727T101703.627495Z"
)
BRIGHTNESS_ANALYSIS = (
    PROJECT_ROOT / "usb-captures" / "analysis"
    / "brightness_medium_to_high" / "write_transactions.json"
)
OUTPUT = (
    PROJECT_ROOT / "usb-captures" / "analysis"
    / "led_mode_breathing_to_full"
)

DEVICE_ADDRESS = 8
APPLICATION_SETUP = (1624, 1724)
INITIAL_READ = (1725, 2502)
PREVIEW = (5895, 5918)
APPLY = (5948, 6259)
APPLY_F3 = (5950, 6248)
MODE_ADDRESS = 0x044C
COMPANION_ADDRESS = 0x044D


class LedModeAnalysisError(RuntimeError):
    """Saved evidence is malformed or contradicts the controlled protocol."""


def load_live_read(directory: Path) -> dict[str, object]:
    try:
        transactions = json.loads(
            (directory / "live_transactions.json").read_text(encoding="utf-8")
        )
        memory_map = json.loads(
            (directory / "live_memory_map.json").read_text(encoding="utf-8")
        )
        present = (directory / "live_memory_present.bin").read_bytes()
        memory = {
            int(address, 16): int(value)
            for address, value in memory_map["bytes"].items()
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LedModeAnalysisError(
            f"malformed live-read directory: {directory}"
        ) from exc
    device = transactions.get("device", {})
    if (
        str(device.get("vendor_id", "")).lower() != "04d9"
        or str(device.get("product_id", "")).lower() != "fc55"
        or str(device.get("interface_number", "")).lower() != "02"
        or device.get("usage_page") != "0xFFA0"
        or device.get("usage") != 1
    ):
        raise LedModeAnalysisError("live read has wrong device/interface/usage")
    if transactions.get("read_count") != 193:
        raise LedModeAnalysisError("live read does not contain 193 requests")
    if memory_map.get("returned_byte_count") != 4604 or len(memory) != 4604:
        raise LedModeAnalysisError("live read does not contain 4,604 bytes")
    for index, record in enumerate(transactions.get("transactions", [])):
        try:
            requested = int(record["length"])
            returned = bytes.fromhex(record["returned_data"])
            response = bytes.fromhex(record["response"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LedModeAnalysisError(
                f"malformed live transaction {index}"
            ) from exc
        if len(returned) != requested or response[8:8 + requested] != returned:
            raise LedModeAnalysisError(
                f"live transaction {index} has missing/malformed data"
            )
    return {
        "directory": str(directory),
        "device": device,
        "read_count": 193,
        "returned_byte_count": 4604,
        "memory": memory,
        "present": present,
        "coverage": tuple(sorted(memory)),
        "presence_sha256": hashlib.sha256(present).hexdigest(),
    }


def compare_live_reads(
    pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
) -> dict[str, object]:
    pre = load_live_read(pre_directory)
    post = load_live_read(post_directory)
    if pre["coverage"] != post["coverage"]:
        raise LedModeAnalysisError("pre/post address coverage differs")
    if pre["present"] != post["present"]:
        raise LedModeAnalysisError("pre/post presence maps differ")
    pre_memory = pre["memory"]
    post_memory = post["memory"]
    changes: list[dict[str, object]] = []
    for address in pre["coverage"]:
        before = pre_memory[address]
        after = post_memory[address]
        if before == after:
            continue
        start = address - 6
        end = address + 6
        changes.append({
            "address": address,
            "address_hex": f"0x{address:04X}",
            "previous": before,
            "previous_hex": f"{before:02X}",
            "new": after,
            "new_hex": f"{after:02X}",
            "surrounding_start": start,
            "surrounding_end": end,
            "pre_surrounding": bytes(
                pre_memory.get(item, 0) for item in range(start, end + 1)
            ).hex(" "),
            "post_surrounding": bytes(
                post_memory.get(item, 0) for item in range(start, end + 1)
            ).hex(" "),
            "classification": (
                "profile-1-lighting-mode-byte"
                if address == MODE_ADDRESS
                else "profile-1-lighting-companion-breath-state-byte"
            ),
            "previous_mapping_status": (
                "previously-unresolved-device-lighting-prefix"
            ),
        })
    return {
        "pre": {key: value for key, value in pre.items()
                if key not in {"memory", "present", "coverage"}},
        "post": {key: value for key, value in post.items()
                 if key not in {"memory", "present", "coverage"}},
        "identical_coverage": True,
        "identical_presence_map": True,
        "changed_addresses": changes,
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


def analyse_led_mode_capture(
    capture: Path = CAPTURE,
    *,
    pre_directory: Path = PRE_READ,
    post_directory: Path = POST_READ,
    brightness_analysis: Path = BRIGHTNESS_ANALYSIS,
) -> dict[str, object]:
    read_comparison = compare_live_reads(pre_directory, post_directory)
    transfers, reads, writes = analyse_feature_capture(
        capture, device_address=DEVICE_ADDRESS
    )
    preview_writes = [
        write for write in writes
        if PREVIEW[0] <= write.transfer.submission_frame <= PREVIEW[1]
    ]
    apply_writes = [
        write for write in writes
        if APPLY_F3[0] <= write.transfer.submission_frame <= APPLY_F3[1]
    ]
    apply_memory = reconstruct_written_memory(apply_writes)
    mode_writes = [
        write for write in writes
        if write.address <= MODE_ADDRESS < write.address + write.block_length
    ]
    commands = Counter(
        transfer.payload[1]
        for transfer in transfers
        if transfer.direction == "SET_REPORT" and len(transfer.payload) >= 2
    )
    brightness = json.loads(brightness_analysis.read_text(encoding="utf-8"))
    old_apply = [
        item for item in brightness["f3_writes"] if item["phase"] == "apply"
    ]
    same_plan = [
        (item["address"], item["block_length"]) for item in old_apply
    ] == [
        (write.address, write.block_length) for write in apply_writes
    ]
    apply_payload_differences = []
    for old, new in zip(old_apply, apply_writes):
        old_data = bytes.fromhex(old["data"])
        if old_data != new.data:
            apply_payload_differences.append({
                "old_frame": old["submission_frame"],
                "new_frame": new.transfer.submission_frame,
                "address": new.address,
                "address_hex": f"0x{new.address:04X}",
                "old_data": old_data.hex(" "),
                "new_data": new.data.hex(" "),
            })
    pre_memory = load_live_read(pre_directory)["memory"]
    apply_changed_from_pre = [
        {
            "address": address,
            "address_hex": f"0x{address:04X}",
            "previous": pre_memory[address],
            "written": value,
        }
        for address, value in sorted(apply_memory.values.items())
        if address in pre_memory and pre_memory[address] != value
    ]
    final_commands = [
        transfer.to_dict()
        for transfer in transfers
        if transfer.direction == "SET_REPORT"
        and APPLY[0] <= transfer.submission_frame <= APPLY[1]
        and len(transfer.payload) >= 2
        and transfer.payload[1] in {0xF1, 0xF5}
    ]
    preview_commands = [
        transfer.to_dict()
        for transfer in transfers
        if transfer.direction == "SET_REPORT"
        and PREVIEW[0] <= transfer.submission_frame <= PREVIEW[1]
    ]
    return {
        "capture": str(capture),
        "device_address": DEVICE_ADDRESS,
        "phases": {
            "application_usb_setup": {
                "first_frame": APPLICATION_SETUP[0],
                "last_frame": APPLICATION_SETUP[1],
                "classification": (
                    "descriptor/application setup; no port reset proven"
                ),
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
        "read_comparison": read_comparison,
        "counts": {
            "feature_transfers": len(transfers),
            "f2_reads": len(reads),
            "f3_writes": len(writes),
            "preview_f3_writes": len(preview_writes),
            "apply_f3_writes": len(apply_writes),
            "apply_payload_bytes": sum(
                write.block_length for write in apply_writes
            ),
            "apply_unique_addresses": len(apply_memory.values),
            "commands": {
                f"{command:02X}": count
                for command, count in sorted(commands.items())
            },
        },
        "feature_transfers": [
            {**transfer.to_dict(), "phase": _phase(transfer.submission_frame)}
            for transfer in transfers
        ],
        "f3_writes": [
            {**write.to_dict(), "phase": _phase(write.transfer.submission_frame)}
            for write in writes
        ],
        "mode_affecting_writes": [write.to_dict() for write in mode_writes],
        "preview_commands": preview_commands,
        "final_commands": final_commands,
        "brightness_apply_comparison": {
            "same_146_block_address_length_plan": same_plan,
            "same_596_byte_sparse_write_size": (
                len(apply_writes) == 146
                and sum(write.block_length for write in apply_writes) == 596
                and len(apply_memory.values) == 596
            ),
            "payload_differences": apply_payload_differences,
            "changed_from_immediate_pre_read": apply_changed_from_pre,
            "macro_region_touched": any(
                write.address + write.block_length > 0x0478
                for write in apply_writes
            ),
        },
    }


def _write_sequence_markdown(
    path: Path,
    title: str,
    transfers: list[dict[str, object]],
) -> None:
    lines = [
        f"# {title}", "",
        "| Frame | Completion | Relative time | Report | Payload |",
        "|---:|---:|---:|---:|---|",
    ]
    for item in transfers:
        lines.append(
            f"| {item['submission_frame']} | {item['completion_frame']} | "
            f"{item['relative_time']:.6f} | {item['report_id']} | "
            f"`{item['payload']}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_analysis_outputs(
    output: Path = OUTPUT,
    *,
    analysis: dict[str, object] | None = None,
) -> None:
    analysis = analysis or analyse_led_mode_capture()
    output.mkdir(parents=True, exist_ok=True)
    (output / "led_mode_transactions.json").write_text(
        json.dumps(analysis, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "led_mode_write_blocks.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "phase", "submission_frame", "completion_frame", "timestamp",
            "report_id", "transfer_length", "address", "block_length",
            "data", "complete_payload",
        ])
        for write in analysis["f3_writes"]:
            writer.writerow([
                write["phase"], write["submission_frame"],
                write["completion_frame"], write["timestamp"],
                write["report_id"], write["declared_transfer_length"],
                write["address_hex"], write["block_length"], write["data"],
                write["complete_payload"],
            ])
    with (output / "pre_vs_post_diff.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "address", "previous", "new", "classification",
            "pre_surrounding", "post_surrounding",
        ])
        for change in analysis["read_comparison"]["changed_addresses"]:
            writer.writerow([
                change["address_hex"], change["previous_hex"],
                change["new_hex"], change["classification"],
                change["pre_surrounding"], change["post_surrounding"],
            ])
    _write_sequence_markdown(
        output / "preview_sequence.md",
        "LED-mode live-preview sequence",
        analysis["preview_commands"],
    )
    apply_sets = [
        item for item in analysis["feature_transfers"]
        if item["phase"] == "apply" and item["direction"] == "SET_REPORT"
    ]
    _write_sequence_markdown(
        output / "apply_sequence.md",
        "LED-mode Apply sequence",
        apply_sets,
    )
    mode_writes = analysis["mode_affecting_writes"]
    changes = analysis["read_comparison"]["changed_addresses"]
    preview = mode_writes[0]
    apply = mode_writes[1]
    report = [
        "# LED mode Breathing → Full Brightness protocol",
        "",
        "Strictly offline analysis; no device was discovered or opened.",
        "",
        "## Confirmed read difference",
        "",
        "Both native reads identify `04D9:FC55`, interface `02`, usage "
        "`FFA0:0001`; each contains 193 valid F2 reads and 4,604 returned "
        "bytes. Their address coverage and presence maps are identical "
        "(`0x002C–0x1417` sparse coverage).",
        "",
    ]
    report.extend(
        f"- `{item['address_hex']}`: `{item['previous_hex']}` → "
        f"`{item['new_hex']}` ({item['classification']})"
        for item in changes
    )
    report.extend([
        "",
        "The controlled transition changed two bytes, not one. Device raw "
        "`02` represented the starting Breathing mode and raw `01` represents "
        "Full Brightness at `0x044C`. The adjacent `0x044D` changed `05→00`, "
        "matching PFD breathing-state values but not independently proving "
        "that semantic mapping.",
        "",
        "## Mode-affecting F3 writes",
        "",
        f"- Preview frame {preview['submission_frame']}: "
        f"`{preview['complete_payload']}`",
        f"- Apply frame {apply['submission_frame']}: "
        f"`{apply['complete_payload']}`",
        "",
        "Both write the six-byte Profile 1 lighting prefix "
        "`FF 00 00 01 00 01` at `0x0449`.",
        "The preview mode-prefix write completed at frame 5898. Nine more "
        "lighting F3 submissions followed before exact lighting `F1 02 04` "
        "at frame 5917. That F1 was submitted 8.963 ms after the final "
        "preview F3 submission (frame 5915). Consecutive preview F3 "
        "submissions were approximately 8.2–8.9 ms apart.",
        "",
        "## Capture phases",
        "",
        "- Application USB setup/descriptor traffic: frames 1624–1724 "
        "(not proof of physical re-enumeration).",
        "- Initial read: frames 1725–2502 (193 F2 transactions bracketed "
        "by F5 00 and F5 01).",
        "- Live preview: frames 5895–5918 (11 F3 writes, then F1 02 04).",
        "- Apply: frames 5948–6259; its 146 F3 writes occupy 5950–6248.",
        "- No F2 verification read occurs after Apply.",
        "",
        "## Apply comparison",
        "",
        f"- Same 146-block address/length plan as brightness Apply: "
        f"{analysis['brightness_apply_comparison']['same_146_block_address_length_plan']}",
        f"- Same 596-byte sparse write size: "
        f"{analysis['brightness_apply_comparison']['same_596_byte_sparse_write_size']}",
        f"- Macro region touched: "
        f"{analysis['brightness_apply_comparison']['macro_region_touched']}",
        "- After the final F3 (frame 6248), Windows sends: "
        "`F1 02 04` (6250), `F1 02 01` (6252), `F1 02 02` (6254), "
        "`F1 02 08` (6256), and `F5 01` (6258). Apply begins with "
        "`F5 00` at frame 5948. Earlier in Apply it also sends `F1 02 02` "
        "(5990), `F1 02 10` (5992 and 6044), and `F1 02 01` (6048).",
        "",
        "Relative to the immediate pre-read, only `0x044C` and `0x044D` "
        "changed among comparable Apply-written addresses. Comparison with "
        "the older brightness capture also shows the already-established "
        "button-1 macro assignment, which is historical state, not mode noise.",
        "",
        "## Candidate sequence and uncertainty",
        "",
        "The smallest sequence actually captured is the eleven lighting writes "
        "listed in `preview_sequence.md`, followed by exact `F1 02 04`. F5 is "
        "absent from preview and present at the end of Apply. The complete "
        "Apply is the only captured sequence followed by demonstrated "
        "persistence. A narrowed six-byte F3 at `0x0449`, a conservative "
        "capture-style delay, and `F1 02 04` is a candidate experiment, not "
        "a proven persistent sequence. A two-byte write "
        "`0x044C: 01 00` is a further inference. No one-byte mode write is "
        "evidence-backed because two persistent bytes changed.",
        "",
        "Windows performs no post-Apply F2 verification. A future guarded "
        "experiment could pre-read the six-byte prefix and read it back, but "
        "no Linux mode writer is implemented here.",
        "",
    ])
    (output / "led_mode_protocol_report.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
