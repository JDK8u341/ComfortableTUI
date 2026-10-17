"""Windows: a standalone C++ provider shares GPU textures with the native layer.

Build the SDK and COMFORTABLE_TUI_BUILD_GPU=1 extension; see docs/native-gpu.md.
Python starts the provider, reads one descriptor, then handles layout and events.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import subprocess

from comfortable_tui import (App, Button, GpuFrameSource, HBox, Label, ListView,
                             OverlayCanvas, Panel, Tabs, Text, VBox)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", type=Path, default=Path(".research/gpu-build/Release/ctui_gpu_producer.exe"))
    parser.add_argument("--fps", type=int, default=240)
    parser.add_argument("--source", default="1280x720")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    width, height = map(int, args.source.lower().split("x"))
    provider = subprocess.Popen([str(args.provider.resolve()), "--width", str(width), "--height", str(height),
                                 "--fps", str(args.fps)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=0x08000000)
    diagnostics = []
    try:
        handshake = provider.stdout.readline().split()
        if len(handshake) != 6 or handshake[0] != "CTUI_GPU_V1":
            raise RuntimeError("GPU provider handshake failed: " + provider.stderr.read())
        source = GpuFrameSource.attach(handshake[1])
        status = Label("C++ transport / D3D11 shared textures / DirectComposition", height=2)

        def event(e):
            if e.kind in ("ready", "metrics", "error", "detached", "mouse_down"):
                diagnostics.append({"kind": e.kind, "data": e.data})
            if e.kind == "metrics":
                status.set_text(f"D3D11 · source → Present return {e.data['frame_age_ms']:.2f} ms · "
                                f"native source updates {e.data['painted_fps']:.0f}/s\n"
                                "The counter is submitted frames, not display FPS; Tab/F6 navigate; Ctrl+Q exits.")
            elif e.kind == "close_request":
                app.exit()
            return False

        def view(**kwargs):
            return OverlayCanvas(source, on_event=event, **kwargs)

        listing = ListView(range(1000), item_height=4,
                           item_widget=lambda i, text: HBox(Label(f"Track {i}", width=12), view()))
        tabs = Tabs({"Video": Panel(view(), "Native C++ / GPU"),
                     "Embedded": VBox(Panel(listing, "Virtualized GPU views"),
                                      Panel(Text("Shared source: ", view(width=20, height=4), " inline"), height=6))})
        root = VBox(status, tabs, HBox(Button("Next tab", lambda: tabs.select(tabs.selected + 1)),
                                      Button("Quit", lambda: app.exit()), height=1))
        with App(root, fps=120) as app:
            if args.smoke_seconds:
                app.call_later(args.smoke_seconds, app.exit)
            app.run()
    finally:
        # App.close() confirms the display worker has released GPU references.
        if provider.poll() is None:
            provider.terminate()
        provider.communicate(timeout=5)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(diagnostics, default=lambda v: asdict(v) if is_dataclass(v) else str(v),
                                             indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
