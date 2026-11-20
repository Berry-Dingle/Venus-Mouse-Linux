"""Offline-only planning and serialization for one confirmed assignment.

This module deliberately has no HID, USB, discovery, transport, or command
line execution surface.  It serializes saved, evidence-confirmed values for
later review; live execution remains unavailable until the minimal sequence
is proven by a dedicated capture.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Final

from .button_assignments import (
    BUTTON_RECORD_LENGTH,
    CONFIRMED_ACTION_LABELS,
    button_assignment_address,
)


REPORT2_LENGTH: Final = 16
ASSIGNMENT_RECORD_LENGTH: Final = BUTTON_RECORD_LENGTH
ASSIGNMENT_F1_REPORT: Final = bytes.fromhex(
    "02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00"
)
ASSIGNMENT_ID_PREFIX: Final = "record-"
LIVE_EXECUTION_STATUS: Final = "blocked: minimal F3/F1 sequence is unproven"


@dataclass(frozen=True, slots=True)
class ConfirmedAssignment:
    """One immutable complete record from the decoder's allowlist."""

    label: str
    raw: bytes

    def __post_init__(self) -> None:
        raw = bytes(self.raw)
        if len(raw) != ASSIGNMENT_RECORD_LENGTH:
            raise ValueError("confirmed assignment must be exactly four bytes")
        object.__setattr__(self, "raw", raw)
        if CONFIRMED_ACTION_LABELS.get(raw) != self.label:
            raise ValueError("assignment is not an exact confirmed record")


def confirmed_assignments() -> tuple[ConfirmedAssignment, ...]:
    """Return immutable assignments generated from the decoder allowlist."""
    return tuple(
        ConfirmedAssignment(label=label, raw=raw)
        for raw, label in CONFIRMED_ACTION_LABELS.items()
    )


def confirmed_assignment(label: str) -> ConfirmedAssignment:
    """Return one allowlisted assignment by its exact semantic label."""
    for assignment in confirmed_assignments():
        if assignment.label == label:
            return assignment
    raise ValueError("assignment label is not confirmed")


def confirmed_assignment_raw(raw: bytes) -> ConfirmedAssignment:
    """Return the allowlisted assignment matching these exact four bytes.

    Labels are descriptive and are not unique identifiers.  This lookup keeps
    duplicate-label records distinct by using the complete raw record as the
    protocol identity.
    """
    value = bytes(raw)
    if len(value) != ASSIGNMENT_RECORD_LENGTH:
        raise ValueError("confirmed assignment must be exactly four bytes")
    label = CONFIRMED_ACTION_LABELS.get(value)
    if label is None:
        raise ValueError("assignment is not an exact confirmed record")
    return ConfirmedAssignment(label=label, raw=value)


def confirmed_assignment_id(assignment: ConfirmedAssignment) -> str:
    """Return the canonical stable ID derived from the complete raw record."""
    selected = _validate_assignment(assignment)
    return ASSIGNMENT_ID_PREFIX + selected.raw.hex().upper()


def confirmed_assignment_from_id(identifier: str) -> ConfirmedAssignment:
    """Resolve one canonical allowlisted record ID without label lookup."""
    if not isinstance(identifier, str):
        raise ValueError("assignment ID must be text")
    if (
        len(identifier) != len(ASSIGNMENT_ID_PREFIX) + 8
        or not identifier.startswith(ASSIGNMENT_ID_PREFIX)
        or identifier != identifier.upper().replace("RECORD-", "record-")
    ):
        raise ValueError("assignment ID is not canonical")
    hex_part = identifier[len(ASSIGNMENT_ID_PREFIX):]
    try:
        raw = bytes.fromhex(hex_part)
    except ValueError as exc:
        raise ValueError("assignment ID is malformed") from exc
    if len(raw) != ASSIGNMENT_RECORD_LENGTH:
        raise ValueError("assignment ID must encode exactly four bytes")
    assignment = confirmed_assignment_raw(raw)
    if confirmed_assignment_id(assignment) != identifier:
        raise ValueError("assignment ID is not canonical")
    return assignment


def _address(profile: int, button: int) -> int:
    if isinstance(profile, bool) or isinstance(button, bool):
        raise ValueError("profile and button must be integers")
    return button_assignment_address(profile, button)


def _validate_assignment(value: ConfirmedAssignment) -> ConfirmedAssignment:
    if not isinstance(value, ConfirmedAssignment):
        raise TypeError("assignment must be a ConfirmedAssignment")
    # Recheck membership so a future caller cannot bypass the shared source
    # of truth with a forged dataclass instance or altered implementation.
    if CONFIRMED_ACTION_LABELS.get(value.raw) != value.label:
        raise ValueError("assignment is not an exact confirmed record")
    return value


def serialize_assignment_f3(
    profile: int,
    button: int,
    assignment: ConfirmedAssignment,
) -> bytes:
    """Serialize exactly one confirmed four-byte assignment F3 report."""
    address = _address(profile, button)
    selected = _validate_assignment(assignment)
    report = (
        bytes.fromhex("02 f3")
        + address.to_bytes(2, "little")
        + bytes.fromhex("04 00 00 00")
        + selected.raw
        + bytes.fromhex("00 00 00 00")
    )
    if len(report) != REPORT2_LENGTH:
        raise AssertionError("assignment F3 serializer produced wrong length")
    return report


@dataclass(frozen=True, slots=True)
class AssignmentWritePlan:
    """One offline plan; it contains no transport or execution method."""

    profile: int
    button: int
    address: int
    source: ConfirmedAssignment
    target: ConfirmedAssignment
    f3_report: bytes
    f1_report: bytes
    evidence_status: str = LIVE_EXECUTION_STATUS

    def __post_init__(self) -> None:
        address = _address(self.profile, self.button)
        source = _validate_assignment(self.source)
        target = _validate_assignment(self.target)
        if self.address != address:
            raise ValueError("plan address does not match profile and button")
        if source == target:
            raise ValueError("source and target assignments must differ")
        if self.f3_report != serialize_assignment_f3(
            self.profile, self.button, target
        ):
            raise ValueError("plan F3 report does not match its target")
        if self.f1_report != ASSIGNMENT_F1_REPORT:
            raise ValueError("plan F1 report is not the observed fixed report")
        if self.evidence_status != LIVE_EXECUTION_STATUS:
            raise ValueError("live execution must remain blocked")
        object.__setattr__(self, "f3_report", bytes(self.f3_report))
        object.__setattr__(self, "f1_report", bytes(self.f1_report))


def build_assignment_write_plan(
    profile: int,
    button: int,
    source: ConfirmedAssignment,
    target: ConfirmedAssignment,
) -> AssignmentWritePlan:
    """Build one immutable offline plan for one confirmed target."""
    address = _address(profile, button)
    source = _validate_assignment(source)
    target = _validate_assignment(target)
    if source == target:
        raise ValueError("source and target assignments must differ")
    return AssignmentWritePlan(
        profile=profile,
        button=button,
        address=address,
        source=source,
        target=target,
        f3_report=serialize_assignment_f3(profile, button, target),
        f1_report=ASSIGNMENT_F1_REPORT,
    )


def safety_audit() -> list[str]:
    """Static audit for the intentionally offline module surface."""
    source = __import__("pathlib").Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {"hid", "hidraw", "usb", "fcntl", "subprocess"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    if imported & forbidden_modules:
        raise RuntimeError("offline assignment module imports live I/O")
    function_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden_functions = {
        "send", "send_report", "write_report", "write_memory", "discover",
        "execute", "main",
    }
    if function_names & forbidden_functions:
        raise RuntimeError("offline assignment module exposes live execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            names = {argument.arg for argument in node.args.args}
            unsafe = names & {
                "payload", "raw", "record", "address_override", "targets",
                "profiles", "records", "assignments",
            }
            if unsafe and node.name != "confirmed_assignment_raw":
                raise RuntimeError("offline API exposes an unsafe generic input")
    if "confirmed_assignment_raw" not in function_names:
        raise RuntimeError("exact raw allowlist lookup is missing")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id in {"open", "system"} for node in ast.walk(tree)):
        raise RuntimeError("offline assignment module performs I/O")
    return [
        "no HID, hidraw, USB, ioctl, subprocess, or discovery imports",
        "no generic report sender, writer, or live command entry point",
        "no arbitrary payload or address override parameter",
        "one immutable target and exact allowlist only",
        "live execution status is structurally blocked",
    ]
