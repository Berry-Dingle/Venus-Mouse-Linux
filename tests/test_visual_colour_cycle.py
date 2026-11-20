from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.known_custom_colour_state import (
    atomic_write_custom_colour_state, initial_state, load_custom_colour_state,
    validate_custom_colour_state,
)
from venus_profile.readonly_hidraw import HidrawDevice
from venus_profile.visual_colour_cycle import (
    COLOUR_PLAN, INITIAL_CONFIRMATION, VisualColourCycleError,
    build_visual_f3, dry_run_lines, run_visual_cycle, safety_audit,
)


def device() -> HidrawDevice:
    return HidrawDevice(Path("/mock/hidraw"), Path("/mock/sysfs"),
                        0x04D9, 0xFC55, 2, 0xFFA0, 1)


def test_fixed_plan_is_exact_and_direct_rgb():
    assert len(COLOUR_PLAN) == 20
    assert [(c.name, c.semantic_hex) for c in COLOUR_PLAN] == [
        ("Red", "#FF0000"), ("Green", "#00FF00"),
        ("Blue", "#0000FF"), ("Yellow", "#FFFF00"),
        ("Cyan", "#00FFFF"), ("Magenta", "#FF00FF"),
        ("White", "#FFFFFF"), ("Black", "#000000"),
        ("Orange", "#FF8000"), ("Purple", "#8000FF"),
        ("Deep Pink", "#FF1493"), ("Deep Sky Blue", "#00BFFF"),
        ("Chartreuse", "#7FFF00"), ("Gold", "#FFD700"),
        ("Saddle Brown", "#8B4513"), ("Turquoise", "#40E0D0"),
        ("Crimson", "#DC143C"), ("Lime Green", "#32CD32"),
        ("Asymmetric Test A", "#12A7E4"),
        ("Asymmetric Test B", "#7B2DD2"),
    ]
    assert COLOUR_PLAN[0].prefix == bytes.fromhex("ff 00 00 01 00 01")
    assert COLOUR_PLAN[1].prefix == bytes.fromhex("00 ff 00 01 00 01")
    assert COLOUR_PLAN[2].prefix == bytes.fromhex("00 00 ff 01 00 01")
    assert COLOUR_PLAN[-2].prefix == bytes.fromhex("12 a7 e4 01 00 01")
    assert COLOUR_PLAN[-1].prefix == bytes.fromhex("7b 2d d2 01 00 01")


def test_exact_reports_and_no_brightness_or_generic_payload():
    for item in COLOUR_PLAN:
        report = build_visual_f3(item)
        assert report[:8] == bytes.fromhex("02 f3 49 04 06 00 00 00")
        assert report[8:14] == item.prefix
        assert report[14:] == b"\0\0"
        assert len(report) == 16
    with pytest.raises(VisualColourCycleError):
        build_visual_f3(type(COLOUR_PLAN[0])(99, "raw", "#123456"))


def test_dry_run_and_audit_are_offline():
    lines = dry_run_lines()
    assert lines[0].startswith("DRY RUN ONLY")
    assert sum(line.startswith("Step ") for line in lines) == 20
    assert "no timer" in lines[1]
    assert len(safety_audit()) >= 8


class FakeTransport:
    def __init__(self):
        self.reports_sent = []
        self.closed = False
        self.calls = []

    def write_one(self, colour):
        self.calls.append(colour.semantic_hex)
        self.reports_sent.extend([
            ("prewrite-f2", bytes.fromhex("02 f2 49 04 06 00 00 00" + "00" * 8)),
            ("colour-f3", build_visual_f3(colour)),
            ("lighting-f1", bytes.fromhex("02 f1 02 04" + "00" * 12)),
            ("postwrite-f2", bytes.fromhex("02 f2 49 04 06 00 00 00" + "00" * 8)),
        ])
        return colour.prefix

    def close(self):
        self.closed = True


def _run(tmp_path: Path, answers: list[str]):
    state = tmp_path / "state.json"
    atomic_write_custom_colour_state(state, initial_state("2026-01-01T00:00:00+00:00"))
    fake = FakeTransport()
    it = iter(answers)
    output = run_visual_cycle(
        device(), output_root=tmp_path / "out", state_path=state,
        transport_factory=lambda dev: fake, input_fn=lambda prompt: next(it),
    )
    return output, state, fake


def test_abort_before_first_write_is_finite_and_safe(tmp_path):
    output, state, fake = _run(tmp_path, [INITIAL_CONFIRMATION, "ABORT"])
    assert fake.calls == []
    assert json.loads((output / "operation.json").read_text())["status"] == "aborted"
    assert load_custom_colour_state(state)["diagnostics"][-1]["status"] == "aborted"


def test_n_observation_and_abort_after_write_records_state(tmp_path):
    output, state, fake = _run(
        tmp_path, [INITIAL_CONFIRMATION, "", "N", "appeared blue", "ABORT"]
    )
    assert fake.calls == ["#FF0000"]
    operation = json.loads((output / "operation.json").read_text())
    assert operation["writes_confirmed"] == 1
    assert operation["partial_run"] is True
    assert operation["last_completed_step"] == 1
    assert operation["visual_n_count"] == 1
    assert "appeared blue" in (output / "visual_observations.tsv").read_text()
    current = load_custom_colour_state(state)
    assert current["current_semantic_hex"] == "#FF0000"
    assert current["diagnostics"][-1]["persistence"] == "untested/pending"


def test_ctrl_c_stops_without_a_write(tmp_path):
    state = tmp_path / "state.json"
    atomic_write_custom_colour_state(state, initial_state("2026-01-01T00:00:00+00:00"))
    def interrupt(_prompt):
        raise KeyboardInterrupt
    fake = FakeTransport()
    output = run_visual_cycle(
        device(), output_root=tmp_path / "out", state_path=state,
        transport_factory=lambda dev: fake, input_fn=interrupt,
    )
    assert fake.calls == []
    assert json.loads((output / "operation.json").read_text())["status"] == "aborted"


def test_lowercase_y_then_enter_advances_to_step_two(tmp_path):
    output, _state, fake = _run(
        tmp_path, [INITIAL_CONFIRMATION, "", "y", "", "ABORT"]
    )
    assert fake.calls == ["#FF0000"]
    operation = json.loads((output / "operation.json").read_text())
    assert operation["steps_attempted"] == 2
    assert operation["visual_y_count"] == 1


def test_all_twenty_steps_have_exactly_one_write_and_no_step_20_advance(tmp_path):
    answers = [INITIAL_CONFIRMATION]
    for number in range(1, 21):
        answers.extend(["", "Y"])
        if number < 20:
            answers.append("")
    output, state, fake = _run(tmp_path, answers)
    operation = json.loads((output / "operation.json").read_text())
    assert operation["status"] == "completed"
    assert operation["steps_attempted"] == 20
    assert operation["writes_confirmed"] == 20
    assert len(fake.calls) == 20
    assert fake.calls[-1] == "#7B2DD2"
    assert load_custom_colour_state(state)["current_semantic_hex"] == "#7B2DD2"
    assert "Press Enter to continue" not in (output / "result.md").read_text()


def test_malformed_visual_and_continuation_responses_reprompt(tmp_path):
    output, _state, fake = _run(
        tmp_path,
        [INITIAL_CONFIRMATION, "", "maybe", "Y", "later", "", "ABORT"],
    )
    assert fake.calls == ["#FF0000"]
    operation = json.loads((output / "operation.json").read_text())
    assert operation["status"] == "aborted"
    assert operation["visual_y_count"] == 1


def test_malformed_initial_confirmation_reprompts(tmp_path):
    output, _state, fake = _run(
        tmp_path, ["wrong", INITIAL_CONFIRMATION, "ABORT"]
    )
    assert fake.calls == []
    assert json.loads((output / "operation.json").read_text())["status"] == "aborted"


def test_eof_does_not_mark_completion(tmp_path):
    state = tmp_path / "state.json"
    atomic_write_custom_colour_state(state, initial_state("2026-01-01T00:00:00+00:00"))
    fake = FakeTransport()
    answers = iter([INITIAL_CONFIRMATION, "", "Y"])
    def eof_after_first(_prompt):
        try:
            return next(answers)
        except StopIteration as exc:
            raise EOFError from exc
    output = run_visual_cycle(
        device(), output_root=tmp_path / "out", state_path=state,
        transport_factory=lambda dev: fake, input_fn=eof_after_first,
    )
    operation = json.loads((output / "operation.json").read_text())
    assert operation["status"] == "aborted"
    assert operation["writes_confirmed"] == 1


def test_validator_rejects_current_matching_neither_history_nor_diagnostic(tmp_path):
    output, state_path, _fake = _run(
        tmp_path, [INITIAL_CONFIRMATION, "", "Y"] + [""]
    )
    document = load_custom_colour_state(state_path)
    document["current_semantic_hex"] = "#010203"
    document["current_semantic_rgb"] = [1, 2, 3]
    document["current_device_rgb"] = "01 02 03"
    document["current_prefix"] = "01 02 03 01 00 01"
    with pytest.raises(Exception, match="conflicts"):
        validate_custom_colour_state(document)


def test_validator_rejects_malformed_or_unconfirmed_diagnostic(tmp_path):
    _output, state_path, _fake = _run(
        tmp_path, [INITIAL_CONFIRMATION] + sum((["", "Y", ""] for _ in range(19)), [])
        + ["", "Y"]
    )
    document = load_custom_colour_state(state_path)
    document["diagnostics"][-1]["last_confirmed_prefix"] = "00 00 00"
    with pytest.raises(Exception, match="prefix"):
        validate_custom_colour_state(document)
    document = load_custom_colour_state(state_path)
    document["diagnostics"][-1]["immediate_read_back_confirmed"] = False
    with pytest.raises(Exception, match="read-back"):
        validate_custom_colour_state(document)
