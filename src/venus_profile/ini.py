"""Small lossless-enough INI model for the editor's simple ASCII files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class IniLine:
    raw: str
    section: str | None = None
    key: str | None = None
    value: str | None = None
    replacement: str | None = None

    def render(self) -> str:
        if self.replacement is not None and self.key is not None:
            return f"{self.key}={self.replacement}"
        return self.raw


@dataclass(slots=True)
class IniDocument:
    lines: list[IniLine]
    newline: str = "\r\n"
    trailing_newline: bool = True
    source_path: Path | None = None
    _index: dict[tuple[str, str], IniLine] = field(default_factory=dict)
    encoding: str = "ascii"

    @classmethod
    def parse_bytes(cls, content: bytes, source_path: Path | None = None) -> "IniDocument":
        # The Windows INI APIs write the ANSI code page; Western systems use
        # cp1252.  Files that are not pure ASCII are decoded with cp1252 and
        # re-encoded with it, so unchanged bytes round-trip exactly.
        try:
            text = content.decode("ascii")
            encoding = "ascii"
        except UnicodeDecodeError:
            text = content.decode("cp1252")
            encoding = "cp1252"
        newline = "\r\n" if "\r\n" in text else "\n"
        trailing = text.endswith(("\r\n", "\n"))
        logical = text.splitlines()
        lines: list[IniLine] = []
        index: dict[tuple[str, str], IniLine] = {}
        section: str | None = None
        for raw in logical:
            stripped = raw.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1]
                lines.append(IniLine(raw=raw, section=section))
            elif section is not None and "=" in raw and not stripped.startswith((";", "#")):
                key, value = raw.split("=", 1)
                line = IniLine(raw=raw, section=section, key=key, value=value)
                lines.append(line)
                index[(section, key)] = line
            else:
                lines.append(IniLine(raw=raw, section=section))
        result = cls(lines, newline, trailing, source_path)
        result._index = index
        result.encoding = encoding
        return result

    @classmethod
    def from_path(cls, path: str | Path) -> "IniDocument":
        source = Path(path)
        return cls.parse_bytes(source.read_bytes(), source)

    @classmethod
    def create(cls, sections: list[tuple[str, list[tuple[str, str]]]]) -> "IniDocument":
        lines: list[IniLine] = []
        index: dict[tuple[str, str], IniLine] = {}
        for section, entries in sections:
            lines.append(IniLine(raw=f"[{section}]", section=section))
            for key, value in entries:
                line = IniLine(raw=f"{key}={value}", section=section, key=key, value=value)
                lines.append(line)
                index[(section, key)] = line
        result = cls(lines)
        result._index = index
        return result

    @property
    def sections(self) -> list[str]:
        return [line.section for line in self.lines
                if line.key is None and line.raw.strip().startswith("[")
                and line.section is not None]

    def items(self, section: str) -> list[tuple[str, str]]:
        return [(line.key, line.replacement if line.replacement is not None else line.value)
                for line in self.lines
                if line.section == section and line.key is not None and line.value is not None]

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        line = self._index.get((section, key))
        if line is None:
            return default
        return line.replacement if line.replacement is not None else line.value

    def require(self, section: str, key: str) -> str:
        value = self.get(section, key)
        if value is None:
            raise ValueError(f"missing [{section}] {key}")
        return value

    def set(self, section: str, key: str, value: str) -> None:
        line = self._index.get((section, key))
        if line is None:
            raise KeyError(f"cannot losslessly add missing [{section}] {key}")
        line.replacement = value

    def rename_section(self, old: str, new: str) -> None:
        """Rename an existing section while retaining header whitespace/order."""
        if old == new:
            return
        headers = [line for line in self.lines
                   if line.key is None and line.section == old
                   and line.raw.strip().startswith("[")]
        if len(headers) != 1:
            raise KeyError(f"expected one [{old}] section, found {len(headers)}")
        for line in self.lines:
            if line.section == old:
                line.section = new
        header = headers[0]
        stripped = header.raw.strip()
        leading = header.raw[:len(header.raw) - len(header.raw.lstrip())]
        trailing = header.raw[len(header.raw.rstrip()):]
        inside = stripped[1:-1]
        left_space = inside[:len(inside) - len(inside.lstrip())]
        right_space = inside[len(inside.rstrip()):]
        header.raw = f"{leading}[{left_space}{new}{right_space}]{trailing}"
        self._rebuild_index()

    def rename_key(self, section: str, old: str, new: str) -> None:
        """Rename one key in a section without changing its value or line order."""
        if old == new:
            return
        matches = [line for line in self.lines
                   if line.section == section and line.key == old]
        if len(matches) != 1:
            raise KeyError(f"expected one [{section}] {old} key, found {len(matches)}")
        line = matches[0]
        left, separator, right = line.raw.partition("=")
        if not separator or left != old:
            raise ValueError(f"cannot preserve formatting while renaming [{section}] {old}")
        line.raw = new + separator + right
        line.key = new
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._index = {
            (line.section, line.key): line
            for line in self.lines
            if line.section is not None and line.key is not None
        }

    def to_bytes(self) -> bytes:
        text = self.newline.join(line.render() for line in self.lines)
        if self.trailing_newline:
            text += self.newline
        return text.encode(self.encoding)
