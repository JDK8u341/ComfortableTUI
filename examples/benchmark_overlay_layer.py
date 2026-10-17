"""Measure shared-layer Qt composition, excluding producer, DWM and display latency."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import platform
import statistics
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import __version__ as qt_version
    from PySide6.QtWidgets import QApplication
    from comfortable_tui import FrameBuffer, PixelRect, Rect
    from comfortable_tui._overlay_qt import _make_layer_class
    application = QApplication([])
    results = []
    for count in (1, 4, 16):
        with ExitStack() as stack:
            buffers = [stack.enter_context(FrameBuffer(160, 90)) for _ in range(count)]
            frame = bytes([255, 124, 184, 255]) * (160 * 90)
            for buffer in buffers:
                buffer.submit(frame)
            columns = math.ceil(math.sqrt(count))
            rows = math.ceil(count / columns)
            specs = []
            for i, buffer in enumerate(buffers):
                rect = Rect((i % columns) * (128 // columns), (i // columns) * (72 // rows),
                            128 // columns - 1, 72 // rows - 1)
                specs.append({"id": i, "buffer": buffer.info, "placement": (rect, rect), "fit": "stretch",
                              "smooth": True, "input_mode": "capture", "enabled": True, "focused": False,
                              "ime": {"cursor": (0, 0, 1, 1), "text": "", "position": 0, "anchor": 0}, "fps": 60})
            layer = _make_layer_class()(lambda event: None)
            try:
                layer.apply({"grid": (128, 72), "items": specs}, PixelRect(0, 0, 1280, 720))
                layer.show()
                application.processEvents()
                for mode in ("one_dirty", "all_dirty"):
                    samples = []
                    for index in range(args.frames + 10):
                        targets = range(count) if mode == "all_dirty" else (0,)
                        for key in targets:
                            buffers[key].submit(frame)  # Producer copy is outside the timed region.
                        started = time.perf_counter()
                        for key in targets:
                            layer.items[key].update()
                        application.processEvents()
                        elapsed = (time.perf_counter() - started) * 1000
                        if index >= 10:
                            samples.append(elapsed)
                    results.append({"canvases": count, "mode": mode,
                                    "median_ms": statistics.median(samples),
                                    "p95_ms": sorted(samples)[min(len(samples) - 1, math.ceil(len(samples) * .95) - 1)],
                                    "native_windows": 1,
                                    "native_children": sum(bool(item.internalWinId()) for item in layer.items.values())})
            finally:
                layer.shutdown()
                application.processEvents()
    report = {"platform": platform.platform(), "python": platform.python_version(), "qt": qt_version,
              "backend": os.environ["QT_QPA_PLATFORM"], "layer_pixels": [1280, 720], "source_pixels": [160, 90],
              "frames": args.frames, "scope": "CPU Qt update/processEvents; excludes producer, DWM and display",
              "results": results}
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
