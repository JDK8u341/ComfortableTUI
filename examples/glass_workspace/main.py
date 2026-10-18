"""A native developer cockpit around an actual terminal. No browser is started."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import subprocess
import threading
import time

from comfortable_tui import (App, Button, FrameBuffer, GpuFrameSource, GlassBackground, GlassCard, GlassCardGroup, GlassControl, GlassTheme,
                             GlassWorkspace, HBox, Label, Log, Panel, VBox)


class Signal:
    """Small CPU sample provider. The UI and composition stay in native code."""
    def __init__(self, buffer):
        self.buffer, self.amplitude, self.marker = buffer, .55, None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.render, name="glass-example-signal", daemon=True)
        self.thread.start()

    def render(self):
        w, h = self.buffer.info.width, self.buffer.info.height
        while not self.stop.is_set():
            started = time.perf_counter()
            pixels = bytearray(bytes((28, 19, 9, 255)) * (w * h))
            for x in range(w):
                y = round(h / 2 + math.sin(x * .045 + started * 2) * h * .35 * self.amplitude)
                for offset in (-1, 0, 1):
                    yy = max(0, min(h - 1, y + offset))
                    at = (yy * w + x) * 4
                    pixels[at:at + 4] = bytes((204, 228, 128, 255))
            if self.marker:
                x = max(0, min(w - 1, round(self.marker[0])))
                for y in range(h):
                    at = (y * w + x) * 4
                    pixels[at:at + 4] = bytes((173, 144, 255, 255))
            self.buffer.submit(pixels)
            self.stop.wait(max(0, .1 - (time.perf_counter() - started)))

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", choices=("orbit", "deck", "ribbon", "focus"), default="orbit")
    parser.add_argument("--material", choices=("glass", "liquid", "frosted", "mica", "solid", "transparent"), default="liquid")
    parser.add_argument("--backdrop", choices=("none", "dim", "frosted", "mica"), default="none")
    parser.add_argument("--reduced-motion", action="store_true")
    parser.add_argument("--gpu-provider", type=Path, help="ctui_glass_signal.exe, built with CMake; keeps frames in C++/GPU")
    parser.add_argument("--preview", action="store_true", help="Use an explicitly labeled visual test owner; not a terminal")
    parser.add_argument("--inspectable-windows", action="store_true", help="Preview-only separate taskbar windows for computer inspection")
    parser.add_argument("--groups", action="store_true", help="Put cards into two retained row containers")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.inspectable_windows and not args.preview:
        parser.error("--inspectable-windows is only available with --preview")
    report = dict(events={}, result="running")
    started = time.perf_counter()
    paused, elapsed, last = False, 0., started
    notes = []
    app = preview = None
    with ExitStack() as stack:
        frames = stack.enter_context(FrameBuffer(320, 96, pixel_format="bgra8_premultiplied"))
        signal = Signal(frames)
        stack.callback(signal.close)
        source = frames
        if args.gpu_provider:
            provider = subprocess.Popen([str(args.gpu_provider.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)
            def stop_provider():
                if provider.poll() is None:
                    provider.stdin.close()
                    try:
                        provider.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        provider.terminate()
                provider.wait(timeout=5)
                provider.stdout.close()
            stack.callback(stop_provider)
            handshake = provider.stdout.readline().split()
            if len(handshake) != 6 or handshake[0] != "CTUI_GPU_V1":
                raise RuntimeError("GPU signal provider handshake failed")
            source = stack.enter_context(GpuFrameSource.attach(handshake[1]))
            signal.close()
        layouts, materials, backdrops = ("orbit", "deck", "ribbon", "focus"), ("liquid", "frosted", "mica", "solid", "transparent"), ("none", "dim", "frosted", "mica")
        initial_material = "liquid" if args.material == "glass" else args.material
        timer = GlassControl("elapsed", "text", "FOCUS SESSION", text="00:00 / 25:00")
        note = GlassControl("note", "input", "Capture a thought…")
        progress = GlassControl("progress", "progress", "Session progress")
        spark = GlassControl("history", "sparkline", "Native control tick · normalized ms")
        layout = GlassControl("layout", "choice", "Card arrangement", options=layouts, value=layouts.index(args.layout))
        material = GlassControl("material", "choice", "Selected card background", options=materials, value=materials.index(initial_material))
        target = GlassControl("target", "choice", "Target card", options=("session", "signal", "health", "appearance"))
        allow_drag = GlassControl("allow_drag", "toggle", "Allow this card to drag", value=1)
        backdrop = GlassControl("backdrop", "choice", "Workspace focus background", options=("none", "dim / no blur", "frosted / Gaussian blur", "mica / wallpaper"), value=backdrops.index(args.backdrop))
        status = Label("Glass workspace starting…", height=1)
        log = Log()
        log.write("A real TUI stays in the terminal. Peripheral cards have native controls.")
        log.write("F6: enter cards. Esc: return. Ctrl+Q: quit. No shell commands run in this demo.")

        def event(e):
            nonlocal paused
            report["events"][e.kind] = report["events"].get(e.kind, 0) + 1
            if e.kind == "change":
                if e.control == "layout":
                    workspace.configure(layout=layouts[int(e.value)])
                elif e.control == "material":
                    card = cards[int(target.value)]
                    card.background = background(materials[int(e.value)])
                    workspace.update()
                elif e.control == "target":
                    card = cards[int(e.value)]
                    material.value = materials.index(card.background.kind)
                    allow_drag.value = float(card.draggable)
                    workspace.update()
                elif e.control == "allow_drag":
                    cards[int(target.value)].draggable = e.value >= .5
                    workspace.update()
                elif e.control == "backdrop":
                    workspace.configure(backdrop=backdrops[int(e.value)])
                elif e.control == "amplitude":
                    signal.amplitude = e.value
                    if args.gpu_provider and provider.poll() is None:
                        provider.stdin.write(f"{e.value}\n")
                        provider.stdin.flush()
                elif e.control == "pause":
                    paused = e.value >= .5
            elif e.kind == "click" and e.control == "reflow":
                workspace.command("reflow")
            elif e.kind in ("click", "submit") and e.control in ("save", "note"):
                if note.text.strip():
                    notes.append(note.text.strip())
                    if app:
                        log.write("Note: " + note.text.strip())
                    note.text = ""
                    workspace.update()
            elif e.kind == "mouse_down":
                signal.marker = e.position
            elif e.kind == "close_request":
                if app:
                    app.exit()
                elif preview:
                    preview.close()

        def background(kind):
            return GlassBackground(kind, tint=0x1A2230 if kind == "solid" else 0xFFFFFF,
                opacity=1 if kind == "solid" else .08 if kind == "frosted" else .025,
                blur=18 if kind == "frosted" else .6)

        cards = [
            GlassCard("session", "Make room for focus.", [timer, note, GlassControl("save", "button", "Keep this thought"),
                      GlassControl("pause", "toggle", "Pause session")], subtitle="A small space for one important thing.", eyebrow="01 / SESSION", width=310),
            GlassCard("signal", "A window into the signal", [GlassControl("wave", "canvas", "LIVE EXTERNAL FRAME", source=source, fit="stretch"),
                      GlassControl("amplitude", "slider", "Signal amplitude", value=.55)], subtitle="Click the canvas to place a marker.", eyebrow="02 / CANVAS", width=330),
            GlassCard("health", "Quietly, making progress.", [spark, progress], subtitle="Live native timing, not simulated CPU usage.", eyebrow="03 / PULSE", width=280),
            GlassCard("appearance", "Make it your space", [target, material, allow_drag, layout, backdrop,
                      GlassControl("reflow", "button", "Restore arranged positions")], subtitle="Each card has its own material and drag policy.", eyebrow="04 / ATMOSPHERE", width=330),
        ]
        for card in cards:
            card.background = background(initial_material)
        if initial_material == "liquid":
            cards[2].background = background("frosted")
        cards[2].draggable = False
        children = [GlassCardGroup("work", cards[:2], layout="row"),
                    GlassCardGroup("tools", cards[2:], layout="row")] if args.groups else cards
        workspace = stack.enter_context(GlassWorkspace(children, layout=args.layout,
            theme=GlassTheme(args.material, reduced_motion=args.reduced_motion), backdrop=args.backdrop, on_event=event))

        def update():
            nonlocal last, elapsed
            now = time.perf_counter()
            if not paused:
                elapsed += now - last
            last = now
            timer.text = f"{int(elapsed) // 60:02}:{int(elapsed) % 60:02} / 25:00"
            progress.value = min(1, elapsed / 1500)
            metrics = workspace.metrics()
            report["last_live_metrics"] = metrics
            spark.series = (*spark.series[-47:], min(1., metrics.get("tick_ms", 0) / 8))
            status.set_text(f"{workspace.layout} · {metrics.get('material', 'starting')} · {len(notes)} notes · "
                            f"{metrics.get('tick_ms', 0):.2f} ms native tick · F6 cards / Esc terminal")
            workspace.update()

        try:
            if args.preview:
                from PySide6.QtCore import QTimer, Qt
                from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
                from .fixtures import OpticalTestBackground
                qt = QApplication.instance() or QApplication([])
                world = OpticalTestBackground()
                world.showMaximized()
                stack.callback(world.close)
                preview = QWidget()
                preview.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                preview.destroyed.connect(lambda: (world.close(), qt.quit()))
                preview.setWindowTitle("Comfortable Glass / Visual test owner")
                preview.resize(560, 420)
                preview.move(450, 160)
                preview.setStyleSheet("QWidget {background:#0c1422;color:#d9e8f5;} QLabel {padding:24px;}")
                box = QVBoxLayout(preview)
                box.addWidget(QLabel("COMFORTABLE / GLASS\n\nVisual test owner\n\nThis is not a terminal.\nThe normal example runs around your real terminal."))
                preview.show()
                workspace.start(int(preview.winId()), inspectable_windows=args.inspectable_windows)
                preview_errors = []
                def checked(callback):
                    try:
                        callback()
                    except Exception as error:
                        preview_errors.append(error)
                        preview.close()
                poller = QTimer()
                poller.setInterval(12)
                poller.timeout.connect(lambda: checked(workspace.poll))
                poller.start()
                updater = QTimer()
                updater.setInterval(500)
                updater.timeout.connect(lambda: checked(update))
                updater.start()
                if args.smoke_seconds:
                    QTimer.singleShot(round(args.smoke_seconds * 1000), preview.close)
                qt.exec()
                if preview_errors:
                    raise preview_errors[0]
            else:
                root = VBox(Label("COMFORTABLE / GLASS     a developer's peripheral workspace", height=1),
                            Panel(log, "WORKSPACE / notes stay here"), status,
                            HBox(Button("Show cards", workspace.show), Button("Hide cards", workspace.hide),
                                 Button("Quit", lambda: app.exit()), height=1), gap=1)
                with App(root) as app:
                    workspace.bind(app)
                    app.bind("f6", lambda: workspace.command("focus_card"))
                    app.bind("f7", lambda: workspace.command("next"))
                    app.call_later(.5, update, repeat=True)
                    if args.smoke_seconds:
                        app.call_later(args.smoke_seconds, app.exit)
                    app.run()
            report.update(result="passed", metrics=workspace.metrics(), notes=len(notes))
        except Exception as error:
            report.update(result="failed", error=str(error))
            raise
        finally:
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
