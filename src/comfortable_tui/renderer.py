"""Double buffering, row-bounded dirty spans and one write per frame."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .canvas import Canvas

try:
    if os.environ.get("COMFORTABLE_TUI_PURE_PYTHON") == "1":
        raise ImportError
    from ._native import diff_spans as _native_diff
except ImportError:
    _native_diff = None


def python_diff(previous: list, current: list, width: int) -> list[tuple[int, int]]:
    if width <= 0 or len(current) % width or len(previous) != len(current):
        raise ValueError("buffers must have equal length and complete rows")
    spans = []
    for row in range(0, len(current), width):
        old, new = previous[row:row + width], current[row:row + width]
        if old == new:
            continue
        start = None
        for x, (a, b) in enumerate(zip(old, new)):
            if a != b:
                if start is None:
                    start = row + x
            elif start is not None:
                spans.append((start, row + x))
                start = None
        if start is not None:
            spans.append((start, row + width))
    return spans


@dataclass
class FrameStats:
    bytes: int = 0
    changed_cells: int = 0
    spans: int = 0


class Renderer:
    def __init__(self, *, synchronized: bool = False, colors: str = "truecolor", native: bool = True):
        if colors not in ("truecolor", "256", "none"):
            raise ValueError("colors must be truecolor, 256, or none")
        self.synchronized, self.colors = synchronized, colors
        self.diff = _native_diff if native and _native_diff else python_diff
        self.backend = "cpp" if self.diff is _native_diff else "python"
        self.previous: Canvas | None = None
        self.stats = FrameStats()

    def invalidate(self):
        self.previous = None

    def encode(self, frame: Canvas) -> str:
        previous = self.previous
        full = previous is None or (previous.width, previous.height) != (frame.width, frame.height)
        if full:
            spans = [(y * frame.width, (y + 1) * frame.width) for y in range(frame.height)]
        elif frame.width:
            spans = self.diff(previous.cells, frame.cells, frame.width)
        else:
            spans = []
        self.stats = FrameStats(changed_cells=sum(b - a for a, b in spans), spans=len(spans))
        # Frames belong to the caller: snapshot protects against in-place drawing.
        self.previous = Canvas(0, 0)
        self.previous.width, self.previous.height = frame.width, frame.height
        self.previous.cells = frame.cells.copy()
        if not spans:
            return ""
        chunks = ["\x1b[?2026h"] if self.synchronized else []
        if full:
            chunks.append("\x1b[2J")
        style = None
        for start, end in spans:
            # Repaint the owning glyph when its continuation is dirty.
            if frame.cells[start].width == 0:
                start -= 1
            if end % frame.width and frame.cells[end - 1].width == 2:
                end += 1
            y, x = divmod(start, frame.width)
            chunks.append(f"\x1b[{y + 1};{x + 1}H")
            for cell in frame.cells[start:end]:
                if cell.width == 0:
                    continue
                if cell.style != style:
                    chunks.append(cell.style.ansi(self.colors))
                    style = cell.style
                chunks.append(cell.text)
        chunks.append("\x1b[0m")
        if self.synchronized:
            chunks.append("\x1b[?2026l")
        result = "".join(chunks)
        self.stats.bytes = len(result.encode("utf-8"))
        return result
