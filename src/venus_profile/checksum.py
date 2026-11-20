"""Win32 WritePrivateProfileStruct-compatible hexadecimal structures."""

from __future__ import annotations

from dataclasses import dataclass


def additive_checksum(data: bytes) -> int:
    """Return the unsigned byte sum used by WritePrivateProfileStruct."""
    return sum(data) & 0xFF


@dataclass(frozen=True, slots=True)
class ProfileStruct:
    """Decoded data plus the checksum byte stored in an INI hex value."""

    data: bytes
    checksum: int
    original_text: str | None = None

    @classmethod
    def decode(cls, text: str) -> "ProfileStruct":
        encoded = bytes.fromhex(text)
        if not encoded:
            raise ValueError("profile structure is empty")
        return cls(encoded[:-1], encoded[-1], text)

    @classmethod
    def from_data(cls, data: bytes) -> "ProfileStruct":
        return cls(bytes(data), additive_checksum(data))

    @property
    def valid(self) -> bool:
        return self.checksum == additive_checksum(self.data)

    def encode(self, *, preserve_text: bool = True) -> str:
        if preserve_text and self.original_text is not None:
            return self.original_text
        return (self.data + bytes([additive_checksum(self.data)])).hex().upper()
