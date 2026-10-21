"""Real native effect chains, independent cards, custom shader and frame hooks.

Run: python -m examples.glass_workspace.effects
Optional: --extension .build/glass-controls (after native_controls/setup.py).
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import math
from pathlib import Path

from comfortable_tui import (App, Button, FrameBuffer, GlassBackground, GlassCanvas, GlassCard,
                             GlassControl, GlassEffect, GlassTheme, GlassWorkspace,
                             Label, Panel, VBox)


class SignalCard(GlassCard):
    """Reusable composition; each instance keeps its own effect description."""
    def __init__(self, id, title, source, tint):
        self.image = GlassCanvas("signal", source, label="Same image source API", height=128,
                                 effects=[GlassEffect("tint", parameters=(.35,), tint=tint)])
        self.intensity = GlassControl("amount", "slider", "Image tint", value=.35)
        self.readout = GlassControl("note", "text", text="Independent native effect",
                                   effects=[GlassEffect("glow", parameters=(1, 10), tint=tint)])
        super().__init__(id, title, [self.image, self.intensity, self.readout], width=300,
                         background=GlassBackground("liquid"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path)
    parser.add_argument("--preview", action="store_true", help="Persistent application-owned GUI fixture; not a terminal")
    args = parser.parse_args()
    with ExitStack() as stack:
        first_source = stack.enter_context(FrameBuffer(160, 80))
        second_source = stack.enter_context(FrameBuffer(160, 80))
        pixels = bytearray(bytes([20, 32, 52, 255]) * 160 * 80)
        for x in range(160):
            y = round(40 + math.sin(x / 11) * 24)
            pixels[(y*160+x)*4:(y*160+x)*4+4] = bytes([255, 240, 170, 255])
        first_source.submit(pixels)
        second_source.submit(pixels)
        first = SignalCard("first", "Independent channel A", first_source, 0x85E5D1)
        second = SignalCard("second", "Independent channel B", second_source, 0xF4A5D1)
        if args.extension:
            from examples.verify_glass_effects import load_effect_extension
            load_effect_extension(args.extension)
            second.image.effects = [GlassEffect("example.negative", parameters=(.35,))]
        workspace = stack.enter_context(GlassWorkspace(
            [first, second], theme=GlassTheme("liquid")))
        request = None

        def event(e):
            if e.kind == "change" and e.control == "amount":
                card = first if e.card == first.id else second
                workspace.set_effect_parameters(card, card.image, 0, [e.value])
            return False

        def animate():
            nonlocal request
            if request:
                request.cancel()

            def frame(clock):
                done = clock.reduced_motion or clock.elapsed >= 1
                strength = 1 if done else 1 + .6*math.sin(clock.elapsed*16)*math.exp(-3*clock.elapsed)
                workspace.set_effect_parameters(first, first.readout, 0, [strength, 10])
                return not done

            request = workspace.request_frames(frame, node=first.readout)

        workspace.on_event = event
        if args.preview:
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
            qt = QApplication.instance() or QApplication([])
            owner = QWidget()
            owner.setWindowTitle("Comfortable Glass / persistent effects preview")
            owner.resize(600, 340)
            owner.setStyleSheet("QWidget {background:#132238;color:#e8f2ff;} QLabel {padding:20px;} QPushButton {padding:12px;}")
            layout = QVBoxLayout(owner)
            layout.addWidget(QLabel("Native effect SDK preview\nTwo independent cards surround this application-owned window.\nTab traverses controls; the sliders affect their own image.\nThis fixture is not a terminal emulator."))
            button = QPushButton("Animate channel A glow")
            button.clicked.connect(animate)
            layout.addWidget(button)
            owner.show()
            workspace.start(int(owner.winId()))
            timer = QTimer()
            timer.timeout.connect(workspace.poll)
            timer.start(16)
            try:
                qt.exec()
            finally:
                workspace.close()
                first.image.canvas.close()
                second.image.canvas.close()
            return
        app = App(Panel(VBox(Label("Native effects around this terminal"),
                             Label("F6 enters cards; Tab traverses; Esc returns."),
                             Label("A/B sliders update independent native parameters."),
                             Button("Animate A glow", on_press=animate),
                             Button("Quit", on_press=lambda: app.exit())), title="Effect SDK"))
        workspace.bind(app)
        try:
            app.run()
        finally:
            workspace.close()
            first.image.canvas.close()
            second.image.canvas.close()


if __name__ == "__main__":
    main()
