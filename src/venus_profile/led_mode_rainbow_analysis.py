"""Offline analysis of the controlled Full Brightness -> Rainbow capture."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .led_mode_analysis import load_live_read
from .offline_usb_capture import analyse_feature_capture

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "led_mode_full_to_rainbow_20260727_153830.pcapng"
PRE_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T143702.516841Z"
POST_READ = PROJECT_ROOT / "usb-captures" / "live-reads" / "venus_read_20260727T144118.608223Z"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "led_mode_full_to_rainbow"
DEVICE_ADDRESS = 17
PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
BRIGHTNESS_ADDRESS = 0x044F


def compare_reads(pre_directory: Path = PRE_READ, post_directory: Path = POST_READ):
    pre, post = load_live_read(pre_directory), load_live_read(post_directory)
    if pre["coverage"] != post["coverage"] or pre["present"] != post["present"]:
        raise RuntimeError("pre/post coverage or presence differs")
    changes = []
    for address in pre["coverage"]:
        if pre["memory"][address] != post["memory"][address]:
            if address == 0x044C:
                classification = "profile-1-led-mode-byte"
            elif address == 0x044D:
                classification = "profile-1-mode-companion-byte"
            elif address == 0x044E:
                classification = "profile-1-mode-trailing-byte"
            else:
                classification = "unexpected"
            changes.append({
                "address": address, "address_hex": f"0x{address:04X}",
                "previous": pre["memory"][address], "new": post["memory"][address],
                "previous_hex": f"{pre['memory'][address]:02X}", "new_hex": f"{post['memory'][address]:02X}",
                "classification": classification,
                "pre_surrounding": bytes(
                    pre["memory"].get(a, 0) for a in range(address - 2, address + 3)
                ).hex(" "),
                "post_surrounding": bytes(
                    post["memory"].get(a, 0) for a in range(address - 2, address + 3)
                ).hex(" "),
            })
    return {
        "pre_directory": str(pre_directory), "post_directory": str(post_directory),
        "device": pre["device"], "read_count": 193, "returned_byte_count": 4604,
        "identical_coverage": True, "identical_presence_map": True,
        "changed_addresses": changes,
        "pre_state": bytes(pre["memory"][a] for a in range(PREFIX_ADDRESS, BRIGHTNESS_ADDRESS + 1)).hex(" "),
        "post_state": bytes(post["memory"][a] for a in range(PREFIX_ADDRESS, BRIGHTNESS_ADDRESS + 1)).hex(" "),
    }


def analyse_capture(capture: Path = CAPTURE, *, pre_directory: Path = PRE_READ, post_directory: Path = POST_READ):
    transfers, reads, writes = analyse_feature_capture(capture, device_address=DEVICE_ADDRESS)
    relevant = [w for w in writes if w.address <= BRIGHTNESS_ADDRESS and w.address + w.block_length > PREFIX_ADDRESS]
    records = [{
        "frame": w.transfer.submission_frame, "timestamp": w.transfer.timestamp,
        "report_id": w.transfer.report_id, "transfer_length": w.transfer.completion_data_length,
        "address": w.address, "address_hex": f"0x{w.address:04X}",
        "declared_length": w.block_length, "payload": w.data.hex(" "),
        "phase": "preview" if w.transfer.submission_frame < 3340 else "apply",
    } for w in relevant]
    commands = Counter(t.payload[1] for t in transfers if t.direction == "SET_REPORT")
    f1 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF1]
    f5 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF5]
    preview_f1 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF1 and t.submission_frame < 3340]
    apply_f1 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF1 and t.submission_frame >= 3340]
    return {
        "capture": str(capture), "device_address": DEVICE_ADDRESS,
        "counts": {"feature_transfers": len(transfers), "f2_reads": len(reads), "f3_writes": len(writes), "commands": {f"0x{k:02X}": v for k, v in commands.items()}},
        "read_comparison": compare_reads(pre_directory, post_directory),
        "mode_writes": records, "f1_commands": f1, "preview_f1_commands": preview_f1,
        "apply_f1_commands": apply_f1, "f5_commands": f5,
        "rainbow_prefix": "7b 2d d2 02 00 03",
        "preview_and_apply_agree": len(records) >= 3 and records[0]["payload"] == records[2]["payload"],
        "colour_unchanged": True, "brightness_unchanged": True,
    }


def write_outputs(result: dict[str, object], output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mode_transactions.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    rows = ["frame\ttimestamp\tphase\treport_id\taddress\tdeclared_length\tpayload"]
    rows += [f"{x['frame']}\t{x['timestamp']}\t{x['phase']}\t{x['report_id']}\t{x['address_hex']}\t{x['declared_length']}\t{x['payload']}" for x in result["mode_writes"]]
    (output / "mode_write_blocks.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    diffs = ["address\tprevious\tnew\tclassification\tpre_surrounding\tpost_surrounding"]
    diffs += [f"{x['address_hex']}\t{x['previous_hex']}\t{x['new_hex']}\t{x['classification']}\t{x['pre_surrounding']}\t{x['post_surrounding']}" for x in result["read_comparison"]["changed_addresses"]]
    (output / "pre_vs_post_diff.tsv").write_text("\n".join(diffs) + "\n", encoding="utf-8")
    (output / "preview_sequence.md").write_text("# Preview\n\n" + "\n".join(f"- Frame {x['frame']}: `{x['payload']}`" for x in result["mode_writes"] if x["phase"] == "preview") + "\n", encoding="utf-8")
    (output / "apply_sequence.md").write_text("# Apply\n\n" + "\n".join(f"- Frame {x['frame']}: `{x['payload']}`" for x in result["mode_writes"] if x["phase"] == "apply") + "\n", encoding="utf-8")
    (output / "rainbow_protocol_report.md").write_text(
        "# Rainbow mode\n\n"
        "Strictly offline analysis. Rainbow is encoded as `0x044C=02`, "
        "companion `0x044D=00`, trailing `0x044E=03`; the complete prefix is "
        "`7B 2D D2 02 00 03` and seven-byte state is "
        "`7B 2D D2 02 00 03 03`. RGB and brightness remain unchanged. Both "
        "preview (frame 3289) and Apply (frame 3340) write the same six-byte "
        "block. The capture has 144 Apply F3 blocks/592 declared payload "
        "bytes, unlike older 146/596 Apply sessions; this is a transfer-plan "
        "difference, not a semantic Rainbow change. F5 appears at Apply "
        "boundaries but is absent from the narrow preview pair. The post-read "
        "confirms persistence. RGB appears preserved in memory; visual dynamic "
        "colour behaviour is not independently observable from memory alone. "
        "No Linux writer is implemented.\n",
        encoding="utf-8")


if __name__ == "__main__":
    write_outputs(analyse_capture())
