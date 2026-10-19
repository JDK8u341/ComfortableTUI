"""Native controls, a regular image canvas and WebViews in the same card system."""
from __future__ import annotations

import math
import time

from comfortable_tui import (FrameBuffer, GlassBackground, GlassCanvas, GlassCard,
                             GlassControl, GpuFrameSource, OverlayCanvas)


class SignalCanvas(OverlayCanvas):
    """Small CPU producer demo. Real applications can supply the same frame API."""
    def __init__(self, source, log):
        super().__init__(source, fit="contain", on_event=self.event)
        self.log, self.paused, self._last_frame = log, False, 0.

    def event(self, event):
        if event.kind == "mouse_down":
            self.paused = not self.paused
            self.log.write("Image provider: " + ("paused" if self.paused else "running"))
            return True
        if event.kind == "transform":
            t = event.data["transform"]
            self.log.write(f"Image viewport {t.viewport_size}, scale {t.scale}, DPI {t.dpi_scale}")
        return False

    def tick(self, dt):
        now = time.perf_counter()
        if self.paused or now - self._last_frame < 1 / 30:
            return
        self._last_frame = now
        w, h = self.buffer.info.width, self.buffer.info.height
        pixels = bytearray(bytes((18, 24, 40, 255)) * w * h)
        for x in range(w):
            y = round(h / 2 + math.sin(x / 13 - now * 3) * h * .32)
            for offset in (-1, 0, 1):
                at = (min(h - 1, max(0, y + offset)) * w + x) * 4
                pixels[at:at + 4] = bytes((110, 230, 200, 255))
        self.buffer.submit(pixels)


def make_canvas(channel, log):
    source = GpuFrameSource.attach(channel) if channel else FrameBuffer(160, 80)
    canvas = OverlayCanvas(source) if channel else SignalCanvas(source, log)
    view = GlassCanvas("image", canvas, label="Standard canvas · click CPU signal to pause", height=140)
    card = GlassCard("image", "Independent image producer", [
        GlassControl("info", "text", text="Same CanvasEvent API; no browser required"), view],
        background=GlassBackground("transparent"), width=320)
    return source, card


def main():
    from .webview import main as run
    run(include_canvas=True)


if __name__ == "__main__":
    main()
