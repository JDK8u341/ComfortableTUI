"""Clipped, grapheme-aware cell framebuffer with wide-cell ownership."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

import regex
from wcwidth import wcswidth

from .style import Style


@dataclass(frozen=True)
class Rect:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def inset(self, padding: int = 1):
        return Rect(self.x + padding, self.y + padding,
                    max(0, self.width - padding * 2), max(0, self.height - padding * 2))

    def intersect(self, other: Rect):
        x, y = max(self.x, other.x), max(self.y, other.y)
        return Rect(x, y, max(0, min(self.x + self.width, other.x + other.width) - x),
                    max(0, min(self.y + self.height, other.y + other.height) - y))


@dataclass(frozen=True)
class Cell:
    text: str = " "
    style: Style = Style()
    width: int = 1  # 0: trailing cell owned by the wide glyph on its left


@lru_cache(maxsize=8192)
def graphemes(text: str) -> tuple[str, ...]:
    return tuple(regex.findall(r"\X", text))


def clean_text(text: str) -> str:
    # User text is data. Never emit embedded terminal control sequences.
    return "".join(c if (ord(c) >= 32 and not 0x7f <= ord(c) < 0xa0) or c == "\n" else " "
                   for c in str(text))


def text_width(text: str) -> int:
    return max(0, wcswidth(clean_text(text)))


def crop_text(text: str, width: int) -> str:
    out, used = [], 0
    for glyph in graphemes(clean_text(text)):
        n = max(0, wcswidth(glyph))
        if glyph == "\n" or used + n > width:
            break
        if n:
            out.append(glyph)
            used += n
    return "".join(out)


class Canvas:
    def __init__(self, width: int, height: int, style: Style = Style()):
        self.width, self.height = max(0, width), max(0, height)
        self.cells = [Cell(style=style)] * (self.width * self.height)
        self.clip = Rect(0, 0, self.width, self.height)
        self.graphics: dict = {}

    @contextmanager
    def clipped(self, rect: Rect):
        old = self.clip
        self.clip = old.intersect(rect)
        try:
            yield self
        finally:
            self.clip = old

    def _erase_owner(self, x: int, y: int):
        index = y * self.width + x
        old = self.cells[index]
        if old.width == 0 and x > 0:
            self.cells[index - 1] = Cell(style=old.style)
        elif old.width == 2 and x + 1 < self.width:
            self.cells[index + 1] = Cell(style=old.style)

    def put(self, x: int, y: int, glyph: str, style: Style = Style()):
        clusters = graphemes(clean_text(glyph))
        glyph = clusters[0] if clusters else ""
        n = wcswidth(glyph)
        if n not in (1, 2) or not self.clip.contains(x, y):
            return
        if n == 2 and not self.clip.contains(x + 1, y):
            return
        self._erase_owner(x, y)
        if n == 2:
            self._erase_owner(x + 1, y)
        index = y * self.width + x
        self.cells[index] = Cell(glyph, style, n)
        if n == 2:
            self.cells[index + 1] = Cell("", style, 0)

    def text(self, x: int, y: int, text: str, style: Style = Style(), width: int | None = None):
        start = x
        limit = self.width - x if width is None else max(0, width)
        for glyph in graphemes(clean_text(text)):
            n = max(0, wcswidth(glyph))
            if glyph == "\n" or x + n > start + limit:
                break
            if n:
                self.put(x, y, glyph, style)
                x += n
        return x - start

    def fill(self, rect: Rect, style: Style, glyph: str = " "):
        area = rect.intersect(self.clip)
        if text_width(glyph) != 1 or len(graphemes(glyph)) != 1:
            raise ValueError("fill glyph must occupy one cell")
        row = [Cell(glyph, style)] * area.width
        for y in range(area.y, area.y + area.height):
            if area.width:
                self._erase_owner(area.x, y)
                self._erase_owner(area.x + area.width - 1, y)
                start = y * self.width + area.x
                self.cells[start:start + area.width] = row

    def box(self, rect: Rect, style: Style, title: str = ""):
        if rect.width < 2 or rect.height < 2:
            return
        x, y, w, h = rect.x, rect.y, rect.width, rect.height
        for yy in (y, y + h - 1):
            self.text(x, yy, "─" * w, style)
        for yy in range(y + 1, y + h - 1):
            self.put(x, yy, "│", style)
            self.put(x + w - 1, yy, "│", style)
        for xx, yy, char in [(x, y, "╭"), (x + w - 1, y, "╮"),
                             (x, y + h - 1, "╰"), (x + w - 1, y + h - 1, "╯")]:
            self.put(xx, yy, char, style)
        if title:
            self.text(x + 2, y, f" {title} ", style, max(0, w - 4))

    def plain(self) -> str:
        return "\n".join("".join(c.text for c in self.cells[y * self.width:(y + 1) * self.width])
                         for y in range(self.height))
