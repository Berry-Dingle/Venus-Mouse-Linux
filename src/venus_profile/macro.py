"""MSMACRO and macro-shaped MSDB models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .actions import ActionRecord
from .checksum import ProfileStruct
from .ini import IniDocument

ACTION_DATA_LENGTH = 189
ACTION_RECORD_CAPACITY = 63
DEFAULT_INSERTED_DELAY_MIN_MS = 0
DEFAULT_INSERTED_DELAY_MAX_MS = 999


def validate_default_inserted_delay_ms(milliseconds: int) -> int:
    if not isinstance(milliseconds, int) or isinstance(milliseconds, bool):
        raise TypeError("default inserted delay must be an integer")
    if not DEFAULT_INSERTED_DELAY_MIN_MS <= milliseconds <= DEFAULT_INSERTED_DELAY_MAX_MS:
        raise ValueError("default inserted delay must be between 0 and 999 ms")
    return milliseconds


@dataclass(slots=True)
class MacroFile:
    document: IniDocument
    source_path: Path | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> "MacroFile":
        source = Path(path)
        return cls(IniDocument.from_path(source), source)

    @classmethod
    def from_bytes(cls, content: bytes) -> "MacroFile":
        return cls(IniDocument.parse_bytes(content))

    @classmethod
    def create(
        cls,
        actions: Iterable[ActionRecord],
        *,
        macro_file_path: str,
        loop_time: int = 1,
        default_inserted_delay_ms: int | None = None,
        default_delay_ms: int | None = None,
        loop_type: int = 2,
        delay_type: int = 2,
        count: int | None = None,
        trailing_records: Iterable[ActionRecord] = (),
    ) -> "MacroFile":
        action_list = list(actions)
        tail = list(trailing_records)
        stored_count = len(action_list) if count is None else count
        all_records = action_list + tail
        if len(all_records) > ACTION_RECORD_CAPACITY:
            raise ValueError("macro action buffer exceeds 63 records")
        if not 0 <= stored_count <= ACTION_RECORD_CAPACITY:
            raise ValueError("Count must be between 0 and 63")
        if default_inserted_delay_ms is not None and default_delay_ms is not None:
            raise ValueError(
                "specify default_inserted_delay_ms, not both delay parameters")
        if default_inserted_delay_ms is None:
            default_inserted_delay_ms = 10 if default_delay_ms is None else default_delay_ms
        validate_default_inserted_delay_ms(default_inserted_delay_ms)
        data = b"".join(record.raw for record in all_records)
        data = data.ljust(ACTION_DATA_LENGTH, b"\0")
        blob = ProfileStruct.from_data(data).encode(preserve_text=False)
        document = IniDocument.create([("Setting", [
            ("MacroFilePath", macro_file_path),
            ("LoopTime", str(loop_time)),
            ("DefDelayTime", str(default_inserted_delay_ms)),
            ("LoopType", str(loop_type)),
            ("DelayType", str(delay_type)),
            ("Count", str(stored_count)),
            ("MacroSetting_0", blob),
        ])])
        return cls(document)

    @property
    def macro_file_path(self) -> str:
        return self.document.require("Setting", "MacroFilePath")

    def _integer(self, key: str) -> int:
        return int(self.document.require("Setting", key), 10)

    @property
    def count(self) -> int:
        return self._integer("Count")

    @property
    def loop_time(self) -> int:
        return self._integer("LoopTime")

    @property
    def loop_type(self) -> int:
        return self._integer("LoopType")

    @property
    def delay_type(self) -> int:
        return self._integer("DelayType")

    @property
    def default_inserted_delay_ms(self) -> int:
        return self._integer("DefDelayTime")

    @property
    def default_delay_ms(self) -> int:
        """Compatibility alias for default_inserted_delay_ms."""
        return self.default_inserted_delay_ms

    def set_default_inserted_delay_ms(self, milliseconds: int) -> None:
        """Set editor metadata without adding or changing action-delay records."""
        validate_default_inserted_delay_ms(milliseconds)
        self.document.set("Setting", "DefDelayTime", str(milliseconds))

    @property
    def has_action_blob(self) -> bool:
        return self.document.get("Setting", "MacroSetting_0") is not None

    @property
    def action_struct(self) -> ProfileStruct:
        return ProfileStruct.decode(self.document.require("Setting", "MacroSetting_0"))

    @property
    def all_records(self) -> list[ActionRecord]:
        data = self.action_struct.data
        if len(data) != ACTION_DATA_LENGTH:
            raise ValueError(f"macro data is {len(data)} bytes, expected 189")
        return [ActionRecord(data[offset:offset + 3])
                for offset in range(0, ACTION_DATA_LENGTH, 3)]

    @property
    def records(self) -> list[ActionRecord]:
        return self.all_records[:self.count]

    def set_actions(self, actions: Iterable[ActionRecord], *, count: int | None = None) -> None:
        records = list(actions)
        if len(records) > ACTION_RECORD_CAPACITY:
            raise ValueError("macro action buffer exceeds 63 records")
        stored_count = len(records) if count is None else count
        if not 0 <= stored_count <= ACTION_RECORD_CAPACITY:
            raise ValueError("Count must be between 0 and 63")
        data = b"".join(record.raw for record in records).ljust(ACTION_DATA_LENGTH, b"\0")
        self.document.set("Setting", "Count", str(stored_count))
        self.document.set(
            "Setting", "MacroSetting_0",
            ProfileStruct.from_data(data).encode(preserve_text=False),
        )

    def to_bytes(self) -> bytes:
        return self.document.to_bytes()
