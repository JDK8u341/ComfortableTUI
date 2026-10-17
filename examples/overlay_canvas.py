"""Windows: external renderer -> shared buffer -> transparent native canvas.

Install: pip install -e ".[overlay]"
Run in a focused Windows Terminal: python examples/overlay_canvas.py
The producer runs in its own process; it does not know about terminal rendering.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import math
import multiprocessing as mp
from pathlib import Path
import queue
import time

from comfortable_tui import (App, Button, FrameBuffer, HBox, Label, OverlayCanvas, Panel,
                             PixelRect, TerminalHost, VBox)


def render_frames(info, controls, stop, fps):
    """Replace this function with your video decoder, game or C++ renderer."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
    cursor, paused = None, False
    start = time.perf_counter()
    with FrameBuffer.attach(info) as buffer:
        while not stop.is_set():
            deadline = time.perf_counter() + 1 / fps
            while True:
                try:
                    command = controls.get_nowait()
                except queue.Empty:
                    break
                if "cursor" in command:
                    cursor = command["cursor"]
                if "paused" in command:
                    paused = command["paused"]
                # transform/resize events are available here too. The example
                # keeps a fixed source resolution and lets the canvas scale it.
            if not paused:
                elapsed = time.perf_counter() - start
                with buffer.write() as pixels:
                    image = QImage(pixels, info.width, info.height, info.stride,
                                   QImage.Format.Format_ARGB32_Premultiplied)
                    painter = QPainter(image)
                    try:
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                        painter.fillRect(image.rect(), QColor(0, 0, 0, 0))
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        gradient = QLinearGradient(0, 0, info.width, info.height)
                        gradient.setColorAt(0, QColor(35, 51, 83, 240))
                        gradient.setColorAt(1, QColor(37, 20, 58, 200))
                        painter.setBrush(gradient)
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawRoundedRect(QRectF(8, 8, info.width - 16, info.height - 16), 24, 24)
                        painter.setPen(QPen(QColor(122, 225, 190, 90), 1))
                        for x in range(24, info.width, 32):
                            painter.drawLine(x, 16, x, info.height - 16)
                        for y in range(24, info.height, 32):
                            painter.drawLine(16, y, info.width - 16, y)
                        painter.setPen(Qt.PenStyle.NoPen)
                        for i in range(6):
                            x = info.width * (.5 + .35 * math.sin(elapsed * .8 + i * .5))
                            y = info.height * (.5 + .32 * math.cos(elapsed + i * .7))
                            painter.setBrush(QColor(255, 124, 184, 180 - i * 20))
                            painter.drawEllipse(QPointF(x, y), 18 + i * 2, 18 + i * 2)
                        if cursor is not None:
                            painter.setPen(QPen(QColor(122, 225, 190), 3))
                            painter.setBrush(Qt.BrushStyle.NoBrush)
                            painter.drawEllipse(QPointF(*cursor), 25, 25)
                    finally:
                        painter.end()
                        del painter, image  # End all native buffer references before publishing.
            time.sleep(max(0, deadline - time.perf_counter()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=lambda s: int(s, 0))
    parser.add_argument("--viewport", type=lambda s: PixelRect(*(float(x) for x in s.split(","))),
                        help="text area x,y,width,height in client-relative physical pixels")
    parser.add_argument("--source", default="640x360", help="source width x height")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--smoke-seconds", type=float, default=0, help="exit automatically for desktop testing")
    parser.add_argument("--report", type=Path, help="write collected diagnostics as JSON on exit")
    args = parser.parse_args()
    width, height = (int(s) for s in args.source.lower().split("x"))
    context = mp.get_context("spawn")
    stop, controls = context.Event(), context.Queue(maxsize=64)
    diagnostics, producer = [], None

    def serialize(value):
        return asdict(value) if is_dataclass(value) else str(value)
    with FrameBuffer(width, height, pixel_format="bgra8_premultiplied") as buffer:
        status = Label("Waiting for native canvas...", height=2)
        paused = False

        def send(command):
            try:
                controls.put_nowait(command)
            except queue.Full:
                pass

        def on_event(event):
            if event.kind not in ("mouse_move", "terminal_mouse"):
                diagnostics.append({"kind": event.kind, "data": event.data, "time": event.timestamp})
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(diagnostics, default=serialize, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
            if event.kind == "transform":
                transform = event.data["transform"]
                status.text = (f"Source {width}x{height}  |  scale {transform.scale[0]:.2f},"
                               f"{transform.scale[1]:.2f}  |  DPI {transform.dpi_scale:.2f}")
                status.invalidate()
                send({"transform": transform})
            elif event.kind == "mouse_down" and event.data["inside_source"]:
                send({"cursor": event.data["source_position"]})
            elif event.kind == "metrics":
                metrics.text = (f"Qt painted {event.data['painted_fps']:.1f} fps  |  "
                                f"draw {event.data['draw_ms']:.2f} ms  |  "
                                f"frame age {event.data['frame_age_ms']:.1f} ms")
                metrics.invalidate()
            elif event.kind == "close_request":
                app.exit()
            return False

        canvas = OverlayCanvas(buffer, on_event=on_event, host=TerminalHost(args.hwnd, args.viewport),
                               fps=args.fps)
        metrics = Label("Click the picture; resize/move the terminal; Tab returns to TUI controls.", height=1)

        def cycle_fit():
            modes = ("contain", "cover", "stretch", "pixel")
            canvas.configure(fit=modes[(modes.index(canvas.fit) + 1) % len(modes)])

        def pause():
            nonlocal paused
            paused = not paused
            send({"paused": paused})

        def modal():
            app.show_modal(Panel(VBox(Label("The native canvas hides behind a TUI modal."),
                                     Button("Close", lambda: app.close_modal())), "Modal", height=6))

        root = VBox(Label("Native buffer canvas · Windows · Ctrl+Q exits", height=1), status,
                    Panel(canvas, "External renderer / shared memory"), metrics,
                    HBox(Button("Scale mode", cycle_fit), Button("Pause / resume", pause),
                         Button("Modal", modal), Button("Quit", lambda: app.exit()), height=1, gap=1), gap=1)
        producer = context.Process(target=render_frames, args=(buffer.info, controls, stop, args.fps))
        try:
            producer.start()
            with App(root, fps=args.fps) as app:
                if args.smoke_seconds:
                    app.call_later(args.smoke_seconds, app.exit)
                app.run()
        except Exception as exc:
            diagnostics.append({"kind": "error", "data": {"message": str(exc), "phase": "application"}})
            raise
        finally:
            stop.set()
            if producer.pid is not None:
                producer.join(timeout=3)
                if producer.is_alive():
                    producer.terminate()
                    producer.join(timeout=3)
            controls.close()
            controls.cancel_join_thread()
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(diagnostics, default=serialize, ensure_ascii=False, indent=2),
                                       encoding="utf-8")


if __name__ == "__main__":
    mp.freeze_support()
    main()
