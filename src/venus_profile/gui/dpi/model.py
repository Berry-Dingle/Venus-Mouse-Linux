"""DPI stage state bound to the selected profile's PFD.

``DPIStageValue`` carries five ``(enable, X, Y, flag)`` uint32 tuples; ``DPI``
is the zero-based stage the mouse starts in.  The displayed DPI comes from
the ``ldcfg.exe`` piecewise formula implemented in :mod:`venus_profile.pfd`.
Only in-memory profile edits happen here; there is no device access.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...pfd import dpi_raw_to_dpi, dpi_to_raw

STAGE_COUNT = 5
DPI_MIN = 200
DPI_MAX = 16400
DPI_RAW_MIN = 1
DPI_RAW_MAX = 106


def confirmed_dpi_values() -> tuple[int, ...]:
    return tuple(
        list(range(200, 6201, 100))
        + list(range(6400, 12401, 200))
        + list(range(12600, 16201, 300))
        + [16400]
    )


def clamp_dpi(dpi: int) -> int:
    if isinstance(dpi, bool) or not isinstance(dpi, int):
        raise ValueError("DPI must be an integer")
    return min(confirmed_dpi_values(), key=lambda value: (abs(value - dpi), value))


@dataclass(frozen=True, slots=True)
class StageView:
    number: int
    enabled_raw: int
    x_raw: int
    y_raw: int
    flag_raw: int

    @property
    def stable_id(self) -> str:
        return f"dpi-stage-{self.number}"

    @property
    def enabled(self) -> bool | None:
        return {0: False, 1: True}.get(self.enabled_raw)

    @property
    def asymmetric(self) -> bool:
        return self.x_raw != self.y_raw

    @property
    def dpi(self) -> int | None:
        return dpi_raw_to_dpi(self.x_raw) if not self.asymmetric else None

    @property
    def x_dpi(self) -> int | None:
        return dpi_raw_to_dpi(self.x_raw)

    @property
    def y_dpi(self) -> int | None:
        return dpi_raw_to_dpi(self.y_raw)

    @property
    def slider_raw(self) -> int:
        return min(max(self.x_raw, DPI_RAW_MIN), DPI_RAW_MAX)

    @property
    def display(self) -> str:
        if self.asymmetric:
            x = f"{self.x_dpi:,}" if self.x_dpi else f"raw {self.x_raw}"
            y = f"{self.y_dpi:,}" if self.y_dpi else f"raw {self.y_raw}"
            return f"X {x} · Y {y}"
        if self.dpi is None:
            return f"Unknown (raw {self.x_raw})"
        return f"{self.dpi:,}"


#: Factory stage values (the mouse's factory read: raw 9/19/39/71/106).
DEFAULT_STAGE_DPIS = (1000, 2000, 4000, 8200, 16400)


class DpiRuleError(ValueError):
    """A DPI edit would leave the profile in an unusable state."""


class ProfileDpiState:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self._listeners = []

    @property
    def profile(self):
        return self.workspace.profile()

    @property
    def stages(self) -> tuple[StageView, ...]:
        profile = self.profile
        result = []
        for number in range(1, STAGE_COUNT + 1):
            value = profile.dpi_stage_value(number)
            result.append(StageView(number, value.enabled, value.x_raw, value.y_raw, value.flag))
        return tuple(result)

    @property
    def default_stage(self) -> int:
        """One-based start-up stage; 0 when the stored value is unknown."""
        raw = self.profile.selected_dpi_stage_raw
        return raw + 1 if 0 <= raw < STAGE_COUNT else 0

    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def changed(self) -> None:
        for listener in tuple(self._listeners):
            listener(self)

    def _edited(self) -> None:
        self.workspace.edited("dpi")
        self.changed()

    def set_dpi(self, stage: int, dpi: int) -> None:
        value = clamp_dpi(dpi)
        current = self.stages[stage - 1]
        raw = dpi_to_raw(value)
        if current.x_raw == raw and current.y_raw == raw:
            return
        self.profile.set_dpi_stage_dpi(stage, value)
        self._edited()

    def set_dpi_raw(self, stage: int, raw: int) -> None:
        dpi = dpi_raw_to_dpi(raw)
        if dpi is None:
            raise ValueError("unsupported DPI step")
        self.set_dpi(stage, dpi)

    def set_enabled(self, stage: int, enabled: bool) -> None:
        stages = self.stages
        current = stages[stage - 1]
        if current.enabled is enabled:
            return
        if not enabled:
            if stage == self.default_stage:
                raise DpiRuleError("The default stage cannot be disabled; choose another default first.")
            if sum(1 for item in stages if item.enabled) <= 1:
                raise DpiRuleError("At least one DPI stage must remain enabled.")
        self.profile.set_dpi_stage_enabled(stage, enabled)
        self._edited()

    def restore_defaults(self) -> bool:
        """Factory DPI values for all five stages, all enabled (user decision, 2026-11-19).

        The start-up stage is kept. Returns False when nothing changed.
        """
        changed = False
        for stage, dpi in enumerate(DEFAULT_STAGE_DPIS, start=1):
            current = self.stages[stage - 1]
            raw = dpi_to_raw(dpi)
            if current.enabled is not True:
                self.profile.set_dpi_stage_enabled(stage, True)
                changed = True
            if current.x_raw != raw or current.y_raw != raw:
                self.profile.set_dpi_stage_dpi(stage, dpi)
                changed = True
        if changed:
            self._edited()
        return changed

    def set_default_stage(self, stage: int) -> None:
        if stage == self.default_stage:
            return
        if self.stages[stage - 1].enabled is False:
            raise DpiRuleError("Enable this stage before making it the default.")
        self.profile.set_selected_dpi_stage(stage)
        self._edited()
