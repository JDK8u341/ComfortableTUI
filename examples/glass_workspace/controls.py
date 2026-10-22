"""Native custom controls, reusable Card and user-written frame animation.

Run in a Windows terminal; this attaches to that terminal, not a fixture host.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import json
from pathlib import Path

from comfortable_tui import (App, Button, GlassCard, GlassControl, GlassTheme, GlassWorkspace,
                             Label, Panel, VBox, register_glass_controls)


def load_extension(path):
    spec = importlib.util.spec_from_file_location("ctui_glass_controls", path.resolve())
    if spec is None or spec.loader is None:
        raise RuntimeError("Build the example extension first; see docs/glass-controls-sdk.md")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register_glass_controls(module)
    return module


class MixerCard(GlassCard):
    """Reusable Python composition; its dial is rendered/handled entirely in C++."""
    def __init__(self, id, title):
        self.dial = GlassControl("gain", "example.dial", "Gain", value=.45)
        self.readout = GlassControl("readout", "text", "Native control", text="Turn with click or Left/Right")
        super().__init__(id, title, [self.dial, self.readout], width=280,
                         subtitle="Independent values / shared native type")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, default=next(Path(".build/glass-controls").glob("ctui_glass_controls*.pyd"), Path(".build/glass-controls/ctui_glass_controls.pyd")))
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    load_extension(args.extension)
    first, second = MixerCard("left", "Channel A"), MixerCard("right", "Channel B")
    workspace = GlassWorkspace([first, second], theme=GlassTheme("liquid"), backdrop="frosted")
    active = None

    def animate():
        nonlocal active
        if active:
            active.cancel()

        def frame(clock):
            done = clock.reduced_motion or clock.elapsed >= .9
            # User-defined, damped oscillation. No framework curve/DSL involved.
            opacity = 1 if done else 1-.65*math.exp(-4*clock.elapsed)*abs(math.sin(16*clock.elapsed))
            workspace.present(first, first.readout, opacity=opacity)
            return not done

        active = workspace.request_frames(frame, node=first.readout)

    def event(e):
        if e.kind == "change" and e.control == "gain":
            card = first if e.card == first.id else second
            card.readout.text = f"Native value: {e.value:.0%}"
            workspace.update()

    workspace.on_event = event
    app = App(VBox(Panel(VBox(Label("Native custom controls around this terminal"),
                              Label("F6: enter cards · Tab: traverse · arrows: adjust dial · Esc: terminal"),
                              Button("Animate A readout", on_press=animate)), title="Glass control SDK")))
    workspace.bind(app)
    if args.smoke_seconds:
        def finish():
            metrics = workspace.metrics()
            from comfortable_tui._win32_host import Win32, _TERMINALS
            api = Win32()
            windows = metrics.get("windows", [])
            owners = [api.user.GetWindow(w["hwnd"], 4) for w in windows]
            result = dict(metrics=metrics, owners=[api.class_name(h) for h in owners], result="failed")
            assert len(windows) == 2 and all(w["visible"] for w in windows)
            assert len(set(owners)) == 1 and all(api.class_name(h) in _TERMINALS for h in owners)
            result["result"] = "passed"
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
            app.exit()
        app.call_later(args.smoke_seconds, finish)
    try:
        app.run()
    finally:
        workspace.close()


if __name__ == "__main__":
    main()
