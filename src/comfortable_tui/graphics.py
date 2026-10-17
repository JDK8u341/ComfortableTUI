"""Small RGB framebuffer, braille fallback and chunked Kitty graphics transport."""
from __future__ import annotations

import base64
import itertools
import math
import zlib

from .canvas import Canvas, Rect
from .style import Style
from .widget import Widget

_ids = itertools.count(1000)


class Surface:
    def __init__(self, width: int, height: int, background=(17, 21, 34)):
        if width <= 0 or height <= 0:
            raise ValueError("surface dimensions must be positive")
        self.width, self.height = width, height
        self.background = background
        self.pixels = bytearray(bytes(background) * (width * height))

    def clear(self, color=None):
        self.background = color or self.background
        self.pixels[:] = bytes(self.background) * (self.width * self.height)

    def pixel(self, x, y, color):
        x, y = round(x), round(y)
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.pixels[i:i + 3] = bytes(color)

    def rect(self, x, y, width, height, color):
        left, top = max(0, round(x)), max(0, round(y))
        right, bottom = min(self.width, round(x + width)), min(self.height, round(y + height))
        if right <= left or bottom <= top:
            return
        row = bytes(color) * (right - left)
        for yy in range(top, bottom):
            start = (yy * self.width + left) * 3
            self.pixels[start:start + len(row)] = row

    def line(self, x0, y0, x1, y1, color):
        n = max(1, math.ceil(max(abs(x1 - x0), abs(y1 - y0))))
        for i in range(n + 1):
            self.pixel(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, color)

    def circle(self, cx, cy, radius, color):
        for y in range(max(0, int(cy - radius)), min(self.height, math.ceil(cy + radius + 1))):
            span = math.sqrt(max(0, radius ** 2 - (y - cy) ** 2))
            self.rect(cx - span, y, span * 2 + 1, 1, color)


class KittyEncoder:
    """Direct RGB/zlib transport; no temp files, subprocesses or shared memory.

    Transfer chunks never exceed 4096 base64 bytes (3072 binary bytes).
    q=2 suppresses acknowledgements, C=1 leaves the text cursor unchanged.
    """
    def __init__(self, image_id=None):
        self.image_id = next(_ids) if image_id is None else image_id
        self.previous = None
        self.previous_rect = None

    def encode(self, surface: Surface, rect: Rect, *, force=False):
        data = bytes(surface.pixels)
        if not force and self.previous == data and self.previous_rect == rect:
            return ""
        self.previous, self.previous_rect = data, rect
        payload = base64.b64encode(zlib.compress(data, 1)).decode("ascii")
        output = [f"\x1b[{rect.y + 1};{rect.x + 1}H"]
        for offset in range(0, len(payload), 4096):
            chunk = payload[offset:offset + 4096]
            more = int(offset + 4096 < len(payload))
            params = (f"a=T,f=24,o=z,s={surface.width},v={surface.height},i={self.image_id},p=1,"
                      f"c={rect.width},r={rect.height},C=1,q=2,m={more}") if offset == 0 else f"m={more},q=2"
            output.append(f"\x1b_G{params};{chunk}\x1b\\")
        return "".join(output)

    def delete(self):
        self.previous = None
        return f"\x1b_Ga=d,d=I,i={self.image_id},q=2\x1b\\"


class PixelCanvas(Widget):
    def __init__(self, width=128, height=64, *, painter=None, animate=False, backend="auto", **kwargs):
        # Pixel dimensions are separate from widget cell width/height.
        cell_width, cell_height = kwargs.pop("cell_width", None), kwargs.pop("cell_height", None)
        super().__init__(width=cell_width, height=cell_height, **kwargs)
        if backend not in ("auto", "braille", "kitty"):
            raise ValueError("backend must be auto, braille, or kitty")
        self.surface = Surface(width, height)
        self.painter, self.animate, self.backend = painter, animate, backend
        self.elapsed = 0.0
        self.encoder = KittyEncoder()
        self._braille_cache = None

    def tick(self, dt):
        if self.animate:
            self.elapsed += dt
            self.invalidate()

    def paint(self, canvas):
        if self.painter:
            self.painter(self.surface, self.elapsed)
        use_kitty = self.backend == "kitty" or self.backend == "auto" and self.app and self.app.graphics_supported
        # An image placement cannot be clipped by a terminal cell scissor.
        # During page transitions or partial scrolling, use the clipped fallback.
        if use_kitty and canvas.clip.intersect(self.rect) == self.rect:
            canvas.graphics[self.encoder.image_id] = (self.encoder, self.surface, self.rect)
            return
        self._braille(canvas)

    def _braille(self, canvas):
        r, surface = self.rect, self.surface
        # Moving UI chrome must not resample an unchanged framebuffer at 60Hz.
        # Snapshot bytes also catches producers writing directly to pixels.
        pixels = bytes(surface.pixels)
        key = r.width, r.height, surface.background, pixels
        if self._braille_cache is not None and self._braille_cache[0] == key:
            canvas.blit(self._braille_cache[1], x=r.x, y=r.y)
            return
        sampled = Canvas(r.width, r.height)
        dots = ((0, 0, 0), (0, 1, 1), (0, 2, 2), (1, 0, 3),
                (1, 1, 4), (1, 2, 5), (0, 3, 6), (1, 3, 7))
        for y in range(r.height):
            for x in range(r.width):
                mask, colors = 0, []
                for dx, dy, bit in dots:
                    sx = min(surface.width - 1, int((x * 2 + dx) * surface.width / max(1, r.width * 2)))
                    sy = min(surface.height - 1, int((y * 4 + dy) * surface.height / max(1, r.height * 4)))
                    i = (sy * surface.width + sx) * 3
                    color = tuple(pixels[i:i + 3])
                    if color != surface.background:
                        mask |= 1 << bit
                        colors.append(color)
                fg = tuple(sum(c[i] for c in colors) // len(colors) for i in range(3)) if colors else (133, 148, 173)
                sampled.put(x, y, chr(0x2800 + mask) if mask else " ", Style(fg, surface.background))
        self._braille_cache = key, sampled
        canvas.blit(sampled, x=r.x, y=r.y)
