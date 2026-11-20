"""Read-only decoding of the confirmed selected DPI-stage byte."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


SELECTED_DPI_STAGE_ADDRESS = 0x0042
SELECTED_DPI_STAGE_MIN = 0
SELECTED_DPI_STAGE_MAX = 4


@dataclass(frozen=True, slots=True)
class SelectedDpiStage:
    """The confirmed zero-based selected-stage value at ``0x0042``."""

    raw_value: int

    def __post_init__(self) -> None:
        if isinstance(self.raw_value, bool) or not isinstance(self.raw_value, int):
            raise ValueError("selected DPI stage must be an integer byte")
        if not SELECTED_DPI_STAGE_MIN <= self.raw_value <= SELECTED_DPI_STAGE_MAX:
            raise ValueError("selected DPI stage must be in the confirmed range 0..4")

    @property
    def selected_stage_index(self) -> int:
        """Return the zero-based selected DPI-stage index."""
        return self.raw_value


def decode_selected_dpi_stage_byte(raw_value: int) -> SelectedDpiStage:
    """Validate and decode one saved byte from the selected-stage address."""
    return SelectedDpiStage(raw_value)


def decode_selected_dpi_stage(memory: Mapping[int, int]) -> SelectedDpiStage:
    """Decode ``0x0042`` from a supplied saved address-to-byte map.

    This function accepts saved bytes only.  It performs no device access,
    discovery, transport, or report construction.
    """
    if SELECTED_DPI_STAGE_ADDRESS not in memory:
        raise ValueError("saved memory is missing address 0x0042")
    return decode_selected_dpi_stage_byte(memory[SELECTED_DPI_STAGE_ADDRESS])
