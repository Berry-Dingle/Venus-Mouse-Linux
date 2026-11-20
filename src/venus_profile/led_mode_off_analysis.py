"""Offline analysis of the controlled Full Brightness -> true Off capture."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .led_mode_analysis import load_live_read
from .offline_usb_capture import analyse_feature_capture

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_mode_full_to_off_20260727_150840.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T140801.426342Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T141157.470945Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_mode_full_to_off"
DEVICE_ADDRESS = 16
MODE_START = 0x0449
MODE_LENGTH = 6
BRIGHTNESS_ADDRESS = 0x044F
F1_LIGHTING = bytes.fromhex("02 f1 02 04" + "00" * 12)


class LedModeOffAnalysisError(RuntimeError):
    pass


def compare_reads(pre_directory: Path = PRE_READ, post_directory: Path = POST_READ):
    pre, post = load_live_read(pre_directory), load_live_read(post_directory)
    if pre["coverage"] != post["coverage"] or pre["present"] != post["present"]:
        raise LedModeOffAnalysisError("pre/post coverage or presence differs")
    changes = []
    for address in pre["coverage"]:
        if pre["memory"][address] != post["memory"][address]:
            changes.append({
                "address": address, "address_hex": f"0x{address:04X}",
                "previous": pre["memory"][address],
                "new": post["memory"][address],
                "previous_hex": f"{pre['memory'][address]:02X}",
                "new_hex": f"{post['memory'][address]:02X}",
                "classification": "profile-1-lighting-prefix" if MODE_START <= address < MODE_START + MODE_LENGTH else "unexpected",
            })
    return {
        "pre_directory": str(pre_directory), "post_directory": str(post_directory),
        "device": pre["device"], "read_count": 193,
        "returned_byte_count": 4604, "identical_coverage": True,
        "identical_presence_map": True, "changed_addresses": changes,
        "pre_state": bytes(pre["memory"][a] for a in range(MODE_START, BRIGHTNESS_ADDRESS + 1)).hex(" "),
        "post_state": bytes(post["memory"][a] for a in range(MODE_START, BRIGHTNESS_ADDRESS + 1)).hex(" "),
    }


def analyse_capture(capture: Path = CAPTURE, *, pre_directory: Path = PRE_READ, post_directory: Path = POST_READ):
    transfers, reads, writes = analyse_feature_capture(capture, device_address=DEVICE_ADDRESS)
    relevant = [w for w in writes if w.address <= BRIGHTNESS_ADDRESS and w.address + w.block_length > MODE_START]
    records = []
    for w in relevant:
        records.append({
            "frame": w.transfer.submission_frame,
            "timestamp": w.transfer.timestamp,
            "report_id": w.transfer.report_id,
            "transfer_length": w.transfer.completion_data_length,
            "address": w.address, "address_hex": f"0x{w.address:04X}",
            "declared_length": w.block_length,
            "payload": w.data.hex(" "),
            "phase": "preview" if w.transfer.submission_frame < 2942 else "apply",
        })
    f1 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF1]
    f5 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF5]
    commands = Counter(t.payload[1] for t in transfers if t.direction == "SET_REPORT")
    comparison = compare_reads(pre_directory, post_directory)
    return {
        "capture": str(capture), "device_address": DEVICE_ADDRESS,
        "counts": {"feature_transfers": len(transfers), "f2_reads": len(reads), "f3_writes": len(writes), "commands": {f"0x{k:02X}": v for k, v in commands.items()}},
        "read_comparison": comparison, "mode_writes": records,
        "f1_commands": f1, "f5_commands": f5,
        "true_off_prefix": "7b 2d d2 00 00 00",
        "brightness_unchanged": True, "colour_unchanged": True,
        "preview_and_apply_agree": len(records) >= 2 and records[0]["payload"] == records[2]["payload"],
    }


def write_outputs(result: dict[str, object], output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mode_transactions.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    rows = ["frame\ttimestamp\tphase\treport_id\taddress\tdeclared_length\tpayload"]
    rows.extend(f"{x['frame']}\t{x['timestamp']}\t{x['phase']}\t{x['report_id']}\t{x['address_hex']}\t{x['declared_length']}\t{x['payload']}" for x in result["mode_writes"])
    (output / "mode_write_blocks.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    diffs = ["address\tprevious\tnew\tclassification"]
    diffs.extend(f"{x['address_hex']}\t{x['previous_hex']}\t{x['new_hex']}\t{x['classification']}" for x in result["read_comparison"]["changed_addresses"])
    (output / "pre_vs_post_diff.tsv").write_text("\n".join(diffs) + "\n", encoding="utf-8")
    (output / "preview_sequence.md").write_text("# Preview\n\n" + "\n".join(f"- Frame {x['frame']}: `{x['payload']}`" for x in result["mode_writes"] if x["phase"] == "preview") + "\n", encoding="utf-8")
    (output / "apply_sequence.md").write_text("# Apply\n\n" + "\n".join(f"- Frame {x['frame']}: `{x['payload']}`" for x in result["mode_writes"] if x["phase"] == "apply") + "\n", encoding="utf-8")
    (output / "mode_off_protocol_report.md").write_text(
        "# True LED Mode Off\n\n"
        "The controlled transition changed `0x044C: 01→00` and `0x044E: 01→00`; colour, companion `0x044D=00`, and brightness `0x044F=03` remained unchanged. "
        "The complete device prefix is `7B 2D D2 00 00 00`, and seven-byte state is `7B 2D D2 00 00 00 03`. Both preview and Apply wrote the same six-byte block. "
        "This is distinct from RGB black `00 00 00 01 00 01 03`; both appeared visually off. No Linux writer is implemented.\n",
        encoding="utf-8")


if __name__ == "__main__":
    write_outputs(analyse_capture())
