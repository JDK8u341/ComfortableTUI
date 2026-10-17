"""Optional native C++ data plane and protocol-negotiated terminal fallback."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys

from comfortable_tui import FrameBuffer, GpuFrameSource, OverlayCanvas, PixelCanvas


class Chart:
    def __init__(self, mode="terminal", *, gpu=False, module_dir=None):
        self.provider, self.source = None, None
        self.values = ()
        self.events = {}
        self._painted = None
        if mode != "terminal":
            root = Path(__file__).resolve().parents[2] / ".research" / "top-native"
            for path in (root, root / "Release", Path(module_dir) if module_dir else root):
                if path.is_dir():
                    sys.path.insert(0, str(path.resolve()))
            native = importlib.import_module("_ctui_top_native")
            if gpu:
                self.provider = native.GpuTrend(640, 192, 60)
                try:
                    self.source = GpuFrameSource.attach(self.provider.channel_name)
                except BaseException:
                    self.provider.close()
                    raise
            else:
                self.source = FrameBuffer(640, 192, pixel_format="bgra8_premultiplied")
                info = self.source.info
                try:
                    self.provider = native.CpuTrend(info.name, info.width, info.height, info.stride, 60)
                except BaseException:
                    self.source.close()
                    raise

    def set_values(self, values):
        self.values = tuple(float(v) for v in values if v is not None)[-180:]
        if self.provider:
            self.provider.set_samples(self.values)

    def widget(self, *, width=None, height=None):
        if self.source:
            # Charts are display-only: terminal mouse/focus stays with their
            # containing list/table, while transform/lifecycle events still flow.
            view = OverlayCanvas(self.source, fit="stretch", input_mode="passthrough",
                                 on_event=self._event, width=width, height=height)
            view.focusable = False
            return view
        return PixelCanvas(240, 72, painter=self._paint, cell_width=width, cell_height=height)

    def _event(self, event):
        self.events[event.kind] = self.events.get(event.kind, 0) + 1
        return False

    def _paint(self, surface, _elapsed):
        if self._painted == self.values:
            return
        self._painted = self.values
        surface.clear()
        for level in (0, 50, 100):
            y = (surface.height - 2) * (1 - level / 100)
            surface.line(0, y, surface.width - 1, y, (40, 49, 68))
        previous = None
        for i, value in enumerate(self.values):
            point = (i * (surface.width - 1) / max(1, len(self.values) - 1),
                     (surface.height - 2) * (1 - min(100, max(0, value)) / 100))
            if previous:
                surface.line(*previous, *point, (119, 224, 194))
            else:
                surface.pixel(*point, (119, 224, 194))
            previous = point

    def close(self):
        # Caller has already closed the App/display consumers.
        if self.provider:
            self.provider.close()
        if self.source:
            self.source.close()


class Charts:
    def __init__(self, mode, *, gpu=False, module_dir=None):
        self.mode, self.gpu, self.module_dir = mode, gpu, module_dir
        self.items = {}

    def get(self, key):
        if key not in self.items:
            self.items[key] = Chart(self.mode, gpu=self.gpu, module_dir=self.module_dir)
        return self.items[key]

    def close(self):
        for chart in self.items.values():
            chart.close()
