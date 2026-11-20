"""Fixed, read-only probe of the bytes the captured read plan skips (user-run).

Hardware test T2 (``docs/APPLY_HARDWARE_TESTS.md``). Full Apply writes, or the
evidence still leaves unexplained, a few bytes that the captured 193-request
read plan never reads. This tool reads exactly those, F2-only, through the
existing ``DeviceSession`` / ``ReadOnlyHidrawTransport`` (identity checks:
04D9:FC55, interface 02, usage FFA0/1):

==========  ======  =====================================================
Address     Length  Why
==========  ======  =====================================================
``0x0020``  10      scroll lines, profiles 1-5 (Apply writes ``01 00`` x5
                    with Report 3; parser field ``WheelScrollLines``)
``0x002C``  2       active profile (confirmed) and ``0x002D`` (never read;
                    the profile switch writes it as ``00``)
``0x003E``  2       unexplained; Apply writes ``00 00``
``0x0446``  4       ``0x0446-0x0449``: unexplained ``0x0446`` (Apply writes
                    ``00 00``), ``0x0448`` (the byte seen in the shifted
                    lighting reply), and profile 1's first colour byte as a
                    cross-check
==========  ======  =====================================================

All start addresses are even, avoiding the odd-address shift seen live
(``LIVE_LIGHTING_PREVIEW_DESIGN.md`` §10.1). Each request is read until two
consecutive replies agree (at most 4). No option accepts an address or
length. Default mode is offline (prints the plan, opens nothing). Static
audit: ``snapshot_audit.read_probe_safety_audit()``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Callable

from .device_session import DeviceSession

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "usb-captures" / "probe-reads"
PROBE_READS: tuple[tuple[int, int, str], ...] = (
    (0x0020, 10, "scroll lines P1-P5 (2 bytes each)"),
    (0x002C, 2, "active profile, 0x002D"),
    (0x003E, 2, "unexplained 0x003E-0x003F"),
    (0x0446, 4, "0x0446-0x0447 unexplained, 0x0448, P1 red"),
)
MAX_READS_PER_REQUEST = 4


class ProbeError(RuntimeError):
    """A probe read did not give two agreeing replies."""


def plan_lines() -> list[str]:
    lines = ["Fixed F2-only probe (no writes):"]
    for address, length, why in PROBE_READS:
        lines.append(f"  0x{address:04X} length {length:2d}  {why}")
    return lines


def _stable_read(session: DeviceSession, address: int, length: int) -> tuple[bytes, list[str]]:
    replies: list[bytes] = []
    for _ in range(MAX_READS_PER_REQUEST):
        replies.append(session.read_memory(address, length))
        if len(replies) >= 2 and replies[-1] == replies[-2]:
            return replies[-1], [reply.hex(" ") for reply in replies]
    raise ProbeError(f"0x{address:04X}: replies never agreed: " + " / ".join(r.hex(" ") for r in replies))


def run_probe(session: DeviceSession) -> list[dict[str, object]]:
    """Read every fixed probe request; returns one record per request."""
    session.open_read_session()
    results = []
    for address, length, why in PROBE_READS:
        value, replies = _stable_read(session, address, length)
        results.append({"address": address, "address_hex": f"0x{address:04X}", "length": length,
                        "purpose": why, "data": value.hex(" "), "replies": replies})
    return results


def interpret(results: list[dict[str, object]]) -> list[str]:
    """Plain-language reading of the probe (inference, for the report)."""
    by_address = {item["address"]: bytes.fromhex(str(item["data"])) for item in results}
    lines = []
    scroll = by_address.get(0x0020)
    if scroll is not None:
        pairs = [scroll[i:i + 2].hex(" ") for i in range(0, 10, 2)]
        lines.append("scroll lines per profile (byte, companion): " + ", ".join(
            f"P{index + 1}={pair}" for index, pair in enumerate(pairs)))
    active = by_address.get(0x002C)
    if active is not None:
        lines.append(f"active profile byte {active[0]:#04x} (profile {active[0] + 1}); 0x002D = {active[1]:#04x}")
    unknown = by_address.get(0x003E)
    if unknown is not None:
        lines.append(f"0x003E-0x003F = {unknown.hex(' ')}")
    tail = by_address.get(0x0446)
    if tail is not None:
        lines.append(f"0x0446-0x0447 = {tail[:2].hex(' ')}; 0x0448 = {tail[2]:#04x}; "
                     f"0x0449 (P1 red) = {tail[3]:#04x}")
    return lines


def _write_evidence(root: Path, device: dict, results: list[dict[str, object]],
                    clock: Callable[[], datetime]) -> Path:
    stamp = clock().strftime("%Y%m%dT%H%M%S.%fZ")
    output = root / f"probe_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    (output / "probe.json").write_text(json.dumps({
        "schema_version": 1, "timestamp_utc": clock().isoformat(), "device": device,
        "operation": "read-only probe (F2 only)", "reads": results,
        "interpretation": interpret(results)}, indent=2) + "\n", encoding="utf-8")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m venus_profile.read_probe")
    parser.add_argument("--live-read", action="store_true",
                        help="explicitly read the fixed probe requests from the mouse (F2 only)")
    return parser


def main(argv: list[str] | None = None, *,
         session_factory: Callable[[], DeviceSession] = DeviceSession.discover,
         input_fn: Callable[[str], str] = input,
         output_root: Path = DEFAULT_OUTPUT_ROOT,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    args = _parser().parse_args(argv)
    for line in plan_lines():
        print(line)
    if not args.live_read:
        print("offline: no hidraw node was discovered or opened (add --live-read to read the mouse)")
        return 0
    session = session_factory()
    with session:
        device = {key: str(value) for key, value in session.describe().items()}
        print(f"selected hidraw node: {device.get('path')}")
        if input_fn("Type READ to perform the fixed F2-only probe: ") != "READ":
            print("confirmation did not match READ; nothing was read")
            return 2
        results = run_probe(session)
    output = _write_evidence(output_root, device, results, clock)
    for item in results:
        print(f"{item['address_hex']} [{item['length']:2d}]  {item['data']}")
    for line in interpret(results):
        print(line)
    print(f"evidence saved: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - live use is user-run
    raise SystemExit(main())
