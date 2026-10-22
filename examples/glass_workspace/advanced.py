"""Terminal-attached native motions, local layouts, HLSL, Canvas and optional WebView.

Build native_controls/setup.py first. See docs/glass-native-motion.md.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path

from comfortable_tui import (App, Button, FrameBuffer, GlassBackground, GlassCanvas, GlassCard,
                             GlassContainer, GlassControl, GlassEffect, GlassMotion, GlassTheme,
                             GlassWorkspace, Label, Panel, VBox, register_glass_motions,
                             register_glass_shader)
from .controls import load_extension


class InstrumentCard(GlassCard):
    """Reusable Card composition; private control/motion instances stay native."""
    def __init__(self, id, title, view):
        self.dial = GlassControl("gain", "example.dial", "Gain", value=.5,
                                 motion=GlassMotion("example.ease"))
        self.status = GlassControl("status", "text", text="Native pulse / own effect instance",
                                   effects=[GlassEffect("example.duotone", parameters=(.5,)),
                                            GlassEffect("glow", target="overlay", parameters=(.6, 8))],
                                   motion=GlassMotion("example.pulse", (8,)))
        body = GlassContainer("body", [
            GlassContainer("instruments", [self.dial, self.status], layout="row", gap=12), view,
        ], padding=8, corner_radius=16)
        super().__init__(id, title, [body], width=420, background=GlassBackground("liquid"),
                         motion=GlassMotion("example.ease"), subtitle="Canvas transport is unchanged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, default=Path(".build/glass-controls"))
    parser.add_argument("--webview", action="store_true", help="Include a separate CEF page inside card B")
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    plugin = next(args.extension.glob("ctui_glass_controls*.pyd")) if args.extension.is_dir() else args.extension
    extension = load_extension(plugin)
    register_glass_motions(extension)
    register_glass_shader(Path(__file__).parent/"shaders/duotone.json")
    with ExitStack() as resources:
        cards = []
        for index in range(2):
            if index and args.webview:
                from comfortable_tui import BrowserService, GlassWebView
                from .webview import local_site
                origin = resources.enter_context(local_site())
                service = resources.enter_context(BrowserService(executable=args.browser))
                page = service.open(origin+"/webview-card.html?name=Native", origins=[origin],
                                    width=360, height=128, render_mode=args.render_mode)
                resources.callback(page.close)
                view = GlassWebView("image", page, height=128, corner_radius=14)
            else:
                source = resources.enter_context(FrameBuffer(160, 80))
                pixels = bytearray(bytes([24, 38, 56, 255])*160*80)
                for x in range(160):
                    y = 15+(x*(index+1)) % 50
                    pixels[(y*160+x)*4:(y*160+x+1)*4] = bytes([210, 240, 130, 255])
                source.submit(pixels)
                view = GlassCanvas("image", source, label="Independent shared image", height=128,
                                   corner_radius=14, effects=[GlassEffect("example.duotone", parameters=(.65,))])
            resources.callback(view.canvas.close)
            cards.append(InstrumentCard(str(index), f"Channel {'AB'[index]}", view))
        workspace = resources.enter_context(GlassWorkspace(cards, theme=GlassTheme("liquid"), backdrop="frosted"))

        def event(e):
            if e.kind == "change" and e.control == "gain":
                card = next(c for c in cards if c.id == e.card)
                workspace.set_effect_parameters(card, card.status, 0, [e.value])
            return False

        def pulse():
            for card in workspace.cards:
                workspace.restart_motion(card, card.status)

        def layout():
            workspace.layout = "focus" if workspace.layout == "orbit" else "orbit"
            workspace.update()

        workspace.on_event = event
        app = App(Panel(VBox(Label("F6: cards · Tab: next · Esc: terminal · Ctrl+Q: quit"),
                             Label("Native motions and HLSL; independent Canvas/WebView sources"),
                             Button("Replay native pulse", on_press=pulse),
                             Button("Change native layout target", on_press=layout),
                             Button("Quit", on_press=lambda: app.exit())), title="Glass extension SDK"))
        workspace.bind(app)
        if args.smoke_seconds:
            def finish():
                from comfortable_tui._win32_host import Win32, _TERMINALS
                metrics = workspace.metrics()
                api = Win32()
                windows = metrics.get("windows", [])
                owners = [api.user.GetWindow(w["hwnd"], 4) for w in windows]
                assert len(windows) == 2 and all(w["visible"] for w in windows)
                assert len(set(owners)) == 1 and all(api.class_name(h) in _TERMINALS for h in owners)
                assert metrics["canvas_frames"] >= 2
                result = dict(result="passed", metrics=metrics, owners=[api.class_name(h) for h in owners],
                              native=extension.instance_stats())
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
                app.exit()
            app.call_later(args.smoke_seconds, finish)
        app.run()


if __name__ == "__main__":
    main()
