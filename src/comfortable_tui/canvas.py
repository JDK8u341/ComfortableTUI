"""Clipped, grapheme-aware cell framebuffer with wide-cell ownership."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import math

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
def _cell(text: str, style: Style, width: int = 1) -> Cell:
    return Cell(text, style, width)


@lru_cache(maxsize=8192)
def graphemes(text: str) -> tuple[str, ...]:
    return tuple(regex.findall(r"\X", text))


def clean_text(text: str) -> str:
    # User text is data. Never emit embedded terminal control sequences.
    return "".join(c if (ord(c) >= 32 and not 0x7f <= ord(c) < 0xa0) or c == "\n" else " "
                   for c in str(text))


@lru_cache(maxsize=2048)
def _short_text_width(text: str) -> int:
    return max(0, wcswidth(clean_text(text)))


def text_width(text: str) -> int:
    text = str(text)
    return _short_text_width(text) if len(text) <= 512 else max(0, wcswidth(clean_text(text)))


def _build_text_run(text, style, limit):
    cells = []
    for glyph in graphemes(clean_text(text)):
        width = max(0, wcswidth(glyph))
        if glyph == "\n" or len(cells) + width > limit:
            break
        if width > 2:
            return None  # Preserve the general text path for unusual clusters.
        if width:
            cells.append(_cell(glyph, style, width))
            if width == 2:
                cells.append(_cell("", style, 0))
    return tuple(cells)


_short_text_run = lru_cache(maxsize=2048)(_build_text_run)


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
        self.cells = [_cell(" ", style)] * (self.width * self.height)
        self.clip = Rect(0, 0, self.width, self.height)
        self.graphics: dict = {}
        self.overlays: dict = {}

    @contextmanager
    def clipped(self, rect: Rect):
        old = self.clip
        self.clip = old.intersect(rect)
        try:
            yield self
        finally:
            self.clip = old

    def style_at(self, x: int, y: int, default: Style = Style()) -> Style:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.cells[y * self.width + x].style
        return default

    def blit(self, source: Canvas, rect: Rect | None = None, *, x=0, y=0, opacity=1.0):
        """Copy cells only; native image placements and input ownership are never copied."""
        if opacity <= 0:
            return
        if source is self:
            snapshot = Canvas(0, 0)
            snapshot.width, snapshot.height, snapshot.cells = self.width, self.height, self.cells.copy()
            source = snapshot
        area = rect or Rect(0, 0, source.width, source.height)
        clipped = area.intersect(Rect(0, 0, source.width, source.height))
        left = max(clipped.x, area.x + self.clip.x - x)
        right = min(clipped.x + clipped.width, area.x + self.clip.x + self.clip.width - x)
        top = max(clipped.y, area.y + self.clip.y - y)
        bottom = min(clipped.y + clipped.height, area.y + self.clip.y + self.clip.height - y)
        styles, cells = {}, {}
        for yy in range(top, bottom):
            start, end = yy * source.width + left, yy * source.width + right
            if end <= start:
                continue
            if source.cells[start].width == 0:
                start += 1
            if end > start and source.cells[end - 1].width == 2:
                end -= 1
            if end <= start:
                continue
            dx, dy = x + start - yy * source.width - area.x, y + yy - area.y
            self._erase_owner(dx, dy)
            self._erase_owner(dx + end - start - 1, dy)
            target = dy * self.width + dx
            row = source.cells[start:end]
            if opacity < 1:
                updated = []
                for index, cell in enumerate(row):
                    base = self.cells[target + index].style
                    key = id(cell), id(base)
                    cached = cells.get(key)
                    if cached is None:
                        style_key = cell.style, base
                        style = styles.get(style_key)
                        if style is None:
                            mixed = base.blend(cell.style, opacity)
                            style = cell.style.with_(fg=mixed.fg, bg=mixed.bg)
                            styles[style_key] = style
                        result = _cell(cell.text, style, cell.width)
                        cells[key] = cell, base, result
                    else:
                        result = cached[2]
                    updated.append(result)
                row = updated
            self.cells[target:target + end - start] = row

    def tint(self, x: float, y: float, width: float, height: float, style: Style, opacity=1.0):
        """Composite a fractional-cell highlight without replacing text or wide-cell owners."""
        if width <= 0 or height <= 0 or opacity <= 0:
            return
        left, right = max(self.clip.x, math.floor(x)), min(self.clip.x + self.clip.width, math.ceil(x + width))
        top, bottom = max(self.clip.y, math.floor(y)), min(self.clip.y + self.clip.height, math.ceil(y + height))
        # Backdrops and whole-cell feedback need one blend per distinct style,
        # not fractional geometry arithmetic for every cell in the viewport.
        if all(v == int(v) for v in (x, y, width, height)) and left < right:
            aligned = all(self.cells[yy * self.width + left].width != 0 and
                          self.cells[yy * self.width + right - 1].width != 2 for yy in range(top, bottom))
            if aligned:
                styles, cells = {}, {}
                for yy in range(top, bottom):
                    start = yy * self.width + left
                    updated = []
                    for cell in self.cells[start:start + right - left]:
                        cached = cells.get(id(cell))
                        if cached is None:
                            color = styles.get(cell.style)
                            if color is None:
                                color = cell.style.blend(style, opacity)
                                if style.reverse and opacity >= .5:
                                    color = color.with_(reverse=not cell.style.reverse)
                                styles[cell.style] = color
                            result = _cell(cell.text, color, cell.width)
                            cells[id(cell)] = cell, result
                        else:
                            result = cached[1]
                        updated.append(result)
                    self.cells[start:start + right - left] = updated
                return
        colors = {}
        cells = {}
        for yy in range(top, bottom):
            coverage_y = max(0, min(yy + 1, y + height) - max(yy, y))
            xx = left
            while xx < right:
                index = yy * self.width + xx
                cell = self.cells[index]
                owner = xx - 1 if cell.width == 0 else xx
                glyph = self.cells[yy * self.width + owner]
                size = glyph.width
                # A clipped wide glyph cannot be recolored in halves.
                if owner < self.clip.x or owner + size > self.clip.x + self.clip.width:
                    xx += 1
                    continue
                coverage_x = max(0, min(owner + size, x + width) - max(owner, x)) / size
                coverage = coverage_x * coverage_y * opacity
                key = glyph.style, coverage
                color = colors.get(key)
                if color is None:
                    color = glyph.style.blend(style, coverage)
                    if style.reverse and coverage >= .5:
                        color = color.with_(reverse=not glyph.style.reverse)
                    colors[key] = color
                for column in range(owner, owner + size):
                    previous = self.cells[yy * self.width + column]
                    key = previous, color
                    updated = cells.get(key)
                    if updated is None:
                        updated = _cell(previous.text, color, previous.width)
                        cells[key] = updated
                    self.cells[yy * self.width + column] = updated
                xx = owner + size

    def _erase_owner(self, x: int, y: int):
        index = y * self.width + x
        old = self.cells[index]
        if old.width == 0 and x > 0:
            self.cells[index - 1] = _cell(" ", old.style)
        elif old.width == 2 and x + 1 < self.width:
            self.cells[index + 1] = _cell(" ", old.style)

    def put(self, x: int, y: int, glyph: str, style: Style = Style()):
        clusters = graphemes(clean_text(glyph))
        glyph = clusters[0] if clusters else ""
        n = wcswidth(glyph)
        self._put_glyph(x, y, glyph, style, n)

    def _put_glyph(self, x: int, y: int, glyph: str, style: Style, n: int):
        # Internal text path already sanitized and segmented this grapheme.
        if n not in (1, 2) or not self.clip.contains(x, y):
            return
        if n == 2 and not self.clip.contains(x + 1, y):
            return
        self._erase_owner(x, y)
        if n == 2:
            self._erase_owner(x + 1, y)
        index = y * self.width + x
        self.cells[index] = _cell(glyph, style, n)
        if n == 2:
            self.cells[index + 1] = _cell("", style, 0)

    def text(self, x: int, y: int, text: str, style: Style = Style(), width: int | None = None):
        start = x
        limit = self.width - x if width is None else max(0, width)
        text = str(text)
        # Reuse immutable cell runs for stable labels, table rows and borders.
        # Clip/copy the whole run, retaining the slow path's logical return width.
        build = _short_text_run if len(text) <= 512 and 0 <= limit <= 512 else _build_text_run
        run = build(text, style, limit)
        if run is not None:
            left, right = max(x, self.clip.x), min(x + len(run), self.clip.x + self.clip.width)
            if self.clip.y <= y < self.clip.y + self.clip.height and left < right:
                if run[left - x].width == 0:
                    left += 1
                if right > left and run[right - x - 1].width == 2:
                    right -= 1
                if left < right:
                    self._erase_owner(left, y)
                    self._erase_owner(right - 1, y)
                    offset = y * self.width + left
                    self.cells[offset:offset + right - left] = run[left - x:right - x]
            return len(run)
        for glyph in graphemes(clean_text(text)):
            n = max(0, wcswidth(glyph))
            if glyph == "\n" or x + n > start + limit:
                break
            if n:
                self._put_glyph(x, y, glyph, style, n)
                x += n
        return x - start

    def fill(self, rect: Rect, style: Style, glyph: str = " "):
        area = rect.intersect(self.clip)
        if text_width(glyph) != 1 or len(graphemes(glyph)) != 1:
            raise ValueError("fill glyph must occupy one cell")
        row = [_cell(glyph, style)] * area.width
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
