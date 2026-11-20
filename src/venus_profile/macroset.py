"""MacroDB/MacroSet.MSDB model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ini import IniDocument

MACRO_SLOT_COUNT = 15
MACRO_SLOT_BASE = 0x0478
MACRO_SLOT_STRIDE = 0x00C8


def report4_address(slot: int) -> int:
    if not 0 <= slot < MACRO_SLOT_COUNT:
        raise ValueError("macro slot must be between 0 and 14")
    return MACRO_SLOT_BASE + slot * MACRO_SLOT_STRIDE


@dataclass(slots=True)
class MacroSet:
    document: IniDocument
    source_path: Path | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> "MacroSet":
        source = Path(path)
        return cls(IniDocument.from_path(source), source)

    @classmethod
    def from_bytes(cls, content: bytes) -> "MacroSet":
        return cls(IniDocument.parse_bytes(content))

    @property
    def names(self) -> list[str]:
        return [self.document.get("MACRO_LIST", str(slot), "") or ""
                for slot in range(MACRO_SLOT_COUNT)]

    def slot_for(self, name: str) -> int | None:
        try:
            return self.names.index(name)
        except ValueError:
            return None

    def set_name(self, slot: int, name: str) -> None:
        if not 0 <= slot < MACRO_SLOT_COUNT:
            raise ValueError("macro slot must be between 0 and 14")
        self.document.set("MACRO_LIST", str(slot), name)

    def to_bytes(self) -> bytes:
        return self.document.to_bytes()
