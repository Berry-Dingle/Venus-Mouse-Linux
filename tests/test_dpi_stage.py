from __future__ import annotations

import json
from pathlib import Path

import pytest

from venus_profile.dpi_stage import (
    SELECTED_DPI_STAGE_ADDRESS,
    SelectedDpiStage,
    decode_selected_dpi_stage,
    decode_selected_dpi_stage_byte,
)


FIXTURES = (
    (0, "venus_read_20260728T092859.409571Z"),
    (1, "venus_read_20260728T092914.377530Z"),
    (2, "venus_read_20260728T092928.246118Z"),
    (3, "venus_read_20260728T092944.934143Z"),
    (4, "venus_read_20260728T093002.332739Z"),
)


def load_memory(project_root: Path, directory: str) -> dict[int, int]:
    path = (
        project_root / "usb-captures" / "live-reads" / directory
        / "live_memory_map.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(address, 0): int(value, 0) if isinstance(value, str) else value
        for address, value in document["bytes"].items()
    }


@pytest.mark.parametrize("expected,directory", FIXTURES)
def test_controlled_indicator_fixtures_decode_zero_based_stage(
    project_root: Path, expected: int, directory: str,
):
    decoded = decode_selected_dpi_stage(load_memory(project_root, directory))
    assert decoded.raw_value == expected
    assert decoded.selected_stage_index == expected


def test_adjacent_controlled_transitions_change_only_0042(project_root: Path):
    memories = [load_memory(project_root, directory) for _, directory in FIXTURES]
    for before, after in zip(memories, memories[1:]):
        changed = [
            address for address in sorted(set(before) | set(after))
            if before.get(address) != after.get(address)
        ]
        assert changed == [SELECTED_DPI_STAGE_ADDRESS]


def test_selected_stage_decoder_is_file_only():
    source = Path(__file__).parents[1] / "src" / "venus_profile" / "dpi_stage.py"
    text = source.read_text(encoding="utf-8")
    assert "/dev/hidraw" not in text
    assert "/dev/bus/usb" not in text
    assert "usb" not in text.lower()
    assert "hid" not in text.lower()
    assert "subprocess" not in text


@pytest.mark.parametrize("bad", [-1, 5, 255, True, "1"])
def test_out_of_range_or_malformed_stage_values_rejected(bad):
    with pytest.raises(ValueError):
        decode_selected_dpi_stage_byte(bad)


def test_missing_stage_address_rejected():
    with pytest.raises(ValueError, match="missing address 0x0042"):
        decode_selected_dpi_stage({})


def test_selected_stage_dataclass_validates_range():
    assert SelectedDpiStage(0).selected_stage_index == 0
    assert SelectedDpiStage(4).selected_stage_index == 4
    with pytest.raises(ValueError, match="range 0..4"):
        SelectedDpiStage(5)
