"""Offline analysis of the controlled Wave/Flash/breathing-speed batch."""

from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path

from .led_mode_analysis import load_live_read
from .offline_usb_capture import analyse_feature_capture

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = PROJECT_ROOT / "usb-captures" / "remaining_lighting_modes_20260727_162641.pcapng"
OUTPUT = PROJECT_ROOT / "usb-captures" / "analysis" / "remaining_lighting_modes"
SNAPSHOTS = OrderedDict([
    ("full_brightness", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T152516.139029Z"),
    ("wave", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T152833.025314Z"),
    ("flash", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T153200.408240Z"),
    ("breathing_default", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T153420.879863Z"),
    ("breathing_low", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T153629.000844Z"),
    ("breathing_high", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T153822.515462Z"),
    ("breathing_ui_full_brightness", PROJECT_ROOT / "usb-captures/live-reads/venus_read_20260727T154117.272912Z"),
])
LIGHTING_START = 0x0449
LIGHTING_END = 0x044F
DEVICE_ADDRESS = 19

STAGE_FRAMES = {
    "full_brightness": (2025, 2065, 2045),
    "wave": (31074, 31135, 31094),
    "flash": (42075, 42115, 42095),
    "breathing_default": (46963, 47002, 46983),
    "breathing_low": (53313, 53352, 53333),
    "breathing_high": (59103, 59143, 59123),
    "breathing_ui_full_brightness": (None, None, None),
}
TRANSITIONS = [
    ("full_brightness", "wave", 2025, 2065, 2045, 2361),
    ("wave", "flash", 31074, 31135, 31094, 31431),
    ("flash", "breathing_default", 42075, 42115, 42095, 42411),
    ("breathing_default", "breathing_low", 46963, 47002, 46983, 47298),
    ("breathing_low", "breathing_high", 53313, 53352, 53333, 53648),
    ("breathing_high", "breathing_ui_full_brightness", 59103, 59143, 59123, 59443),
]


def validate_snapshots() -> dict[str, object]:
    result = OrderedDict()
    for name, path in SNAPSHOTS.items():
        data = load_live_read(path)
        prefix = bytes(data["memory"][a] for a in range(LIGHTING_START, LIGHTING_END + 1))
        result[name] = {
            "path": str(path), "device": data["device"],
            "read_count": data["read_count"],
            "returned_byte_count": data["returned_byte_count"],
            "lighting_state": prefix.hex(" "),
            "coverage_sha256": __import__("hashlib").sha256(bytes(data["present"])).hexdigest(),
        }
    first = result[next(iter(result))]
    for item in result.values():
        if item["device"]["vendor_id"].lower() != "04d9" or item["device"]["product_id"].lower() != "fc55":
            raise ValueError("snapshot has wrong VID/PID")
        if item["device"]["interface_number"] != "02" or item["device"]["usage_page"] != "0xFFA0" or item["device"]["usage"] != 1:
            raise ValueError("snapshot has wrong interface/usage")
        if item["read_count"] != 193 or item["returned_byte_count"] != 4604:
            raise ValueError("snapshot is incomplete")
        if item["coverage_sha256"] != first["coverage_sha256"]:
            raise ValueError("snapshot presence maps differ")
    return {"snapshots": result, "identical_coverage": True, "identical_presence_maps": True}


def pairwise_diffs() -> list[dict[str, object]]:
    loaded = {name: load_live_read(path) for name, path in SNAPSHOTS.items()}
    names = list(SNAPSHOTS)
    rows = []
    for before_name, after_name in zip(names, names[1:]):
        before, after = loaded[before_name]["memory"], loaded[after_name]["memory"]
        changed = []
        for address in range(LIGHTING_START, LIGHTING_END + 1):
            if before[address] != after[address]:
                changed.append({"address": f"0x{address:04X}", "previous": f"{before[address]:02X}", "new": f"{after[address]:02X}"})
        rows.append({"before": before_name, "after": after_name, "changed": changed})
    return rows


def analyse_capture() -> dict[str, object]:
    transfers, reads, writes = analyse_feature_capture(CAPTURE, device_address=DEVICE_ADDRESS)
    selected = []
    for write in writes:
        if write.address == LIGHTING_START and write.block_length == 6:
            selected.append({
                "frame": write.transfer.submission_frame,
                "timestamp": write.transfer.timestamp,
                "report_id": write.transfer.report_id,
                "declared_length": write.block_length,
                "payload": write.data.hex(" "),
                "phase": "preview" if write.transfer.submission_frame in {2025,31074,42075,46963,53313,59103} else "apply",
            })
    f1 = [t.to_dict() for t in transfers if t.direction == "SET_REPORT" and t.payload[1] == 0xF1]
    f1_by_frame = {item["submission_frame"]: item for item in f1}
    correlations = []
    for before, after, preview, apply, preview_f1, apply_f1 in TRANSITIONS:
        correlations.append({
            "before": before, "after": after,
            "preview_f3_frame": preview, "apply_f3_frame": apply,
            "preview_f1_frame": preview_f1, "apply_f1_frame": apply_f1,
            "preview_f1": f1_by_frame.get(preview_f1, {}).get("payload"),
            "apply_f1": f1_by_frame.get(apply_f1, {}).get("payload"),
        })
    return {
        "device_address": DEVICE_ADDRESS,
        "feature_transfers": len(transfers), "f2_reads": len(reads), "f3_writes": len(writes),
        "mode_writes": selected, "f1_commands": f1,
        "stage_frames": STAGE_FRAMES, "transition_correlations": correlations,
    }


def analyse() -> dict[str, object]:
    return {"snapshot_validation": validate_snapshots(), "pairwise_diffs": pairwise_diffs(), "capture": analyse_capture()}


def write_outputs(output: Path = OUTPUT) -> dict[str, object]:
    result = analyse()
    output.mkdir(parents=True, exist_ok=True)
    (output / "remaining_lighting_analysis.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (output / "snapshot_states.tsv").open("w", encoding="utf-8") as f:
        f.write("stage\tpath\tstate_0449_044f\n")
        for stage, item in result["snapshot_validation"]["snapshots"].items():
            f.write(f"{stage}\t{item['path']}\t{item['lighting_state']}\n")
    with (output / "pairwise_diffs.tsv").open("w", encoding="utf-8") as f:
        f.write("before\tafter\taddress\tprevious\tnew\n")
        for row in result["pairwise_diffs"]:
            for item in row["changed"]:
                f.write(f"{row['before']}\t{row['after']}\t{item['address']}\t{item['previous']}\t{item['new']}\n")
    correlation_lines = "\n".join(
        f"- {x['before']} → {x['after']}: preview F3 {x['preview_f3_frame']}, "
        f"preview F1 {x['preview_f1_frame']}, Apply F3 {x['apply_f3_frame']}, "
        f"Apply F1 {x['apply_f1_frame']}"
        for x in result["capture"]["transition_correlations"]
    )
    (output / "remaining_lighting_protocol_report.md").write_text(
        "# Remaining lighting-mode batch (offline)\n\n"
        "All seven snapshots are complete 193-read plans with 4,604 bytes and "
        "matching coverage. States are: Full `7B 2D D2 01 00 01 03`; Wave "
        "`7B 2D D2 03 00 01 03`; Flash `7B 2D D2 04 05 01 03`; Breathing "
        "default `7B 2D D2 02 05 01 03`; Low `7B 2D D2 02 08 01 03`; High "
        "`7B 2D D2 02 02 01 03`; UI-labelled Full Brightness speed "
        "`7B 2D D2 02 00 01 03`. Wave and Flash mode transitions change the "
        "triplet; speed-only transitions change only 0x044D. The UI label "
        "Full Brightness is retained and is not conflated with mode Full "
        "Brightness. No live writer was added.\n\n"
        "## Capture correlations\n\n" + correlation_lines + "\n",
        encoding="utf-8")
    return result


if __name__ == "__main__":
    write_outputs()
