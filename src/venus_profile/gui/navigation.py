"""Single-selection navigation state for the application shell."""

from __future__ import annotations

from dataclasses import dataclass


PAGE_NAMES = ("buttons", "lighting", "macros", "dpi", "settings")


@dataclass(slots=True)
class NavigationState:
    selected_page: str = "buttons"

    def select(self, page: str) -> str:
        if page not in PAGE_NAMES:
            raise ValueError(f"unknown GUI page {page!r}")
        self.selected_page = page
        return page
