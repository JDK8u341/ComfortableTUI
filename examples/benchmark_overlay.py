"""Measure shared-buffer transport and offscreen Qt drawing, not monitor FPS."""
import argparse
import json
import platform
import statistics
import time

from comfortable_tui import FrameBuffer


def summary(values):
    ordered = sorted(values)
    return {"median_ms": statistics.median(values), "p95_ms": ordered[int((len(ordered) - 1) * .95)]}


def benchmark(width, height, frames, qt):
    if qt:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        target = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    with FrameBuffer(width, height, pixel_format="bgra8_premultiplied") as buffer:
        pixels = bytes([100, 80, 220, 255]) * (width * height)
        buffer.submit(pixels)
        buffer.submit(pixels)  # Both alternating slots contain an initialized opaque image.
        copied, direct, read, draw = [], [], [], []
        for i in range(frames + 10):
            start = time.perf_counter()
            buffer.submit(pixels)
            submit_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            with buffer.write() as view:
                view[0] = i % 256  # Transport overhead only, excludes pixel generation.
            direct_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            with buffer.read() as lease:
                read_ms = (time.perf_counter() - start) * 1000
                if qt:
                    image = QImage(lease.pixels, width, height, buffer.info.stride,
                                   QImage.Format.Format_ARGB32_Premultiplied)
                    started = time.perf_counter()
                    painter = QPainter(target)
                    painter.drawImage(QRectF(0, 0, width, height), image)
                    painter.end()
                    draw_ms = (time.perf_counter() - started) * 1000
                    del painter, image
            if i >= 10:
                copied.append(submit_ms)
                direct.append(direct_ms)
                read.append(read_ms)
                if qt:
                    draw.append(draw_ms)
        return {"size": [width, height], "slots": 3, "frame_bytes": buffer.info.frame_bytes,
                "submit_copy": summary(copied), "write_lease_overhead": summary(direct),
                "read_lease_overhead": summary(read), "qt_offscreen_draw": summary(draw) if qt else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--qt", action="store_true")
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("frames must be positive")
    print(json.dumps({"platform": platform.platform(), "python": platform.python_version(),
                      "frames": args.frames, "note": "offscreen CPU timings; not DWM/GPU/presentation latency",
                      "results": [benchmark(w, h, args.frames, args.qt)
                                  for w, h in ((640, 360), (1920, 1080), (3840, 2160))]}, indent=2))
