"""An in-process C++ rendering thread; Python only connects and controls the view.

Build examples/native first; see docs/native-gpu.md, '进程内提供者示例'.
The display helper remains a separate process, shared with the other canvases.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from comfortable_tui import App, GpuFrameSource, Label, OverlayCanvas, Panel, VBox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".research/inprocess-build/Release")
    parser.add_argument("--fps", type=int, default=120)
    parser.add_argument("--check", action="store_true", help="Check GPU production while holding the GIL")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    args = parser.parse_args()
    sys.path.insert(0, str(args.module_dir.resolve()))
    from _ctui_inprocess_example import InprocessProvider

    provider = InprocessProvider(1280, 720, args.fps)
    source = None
    try:
        source = GpuFrameSource.attach(provider.channel_name)
        if args.check:
            # Prevent Python thread switches while the C++ worker must keep publishing.
            previous = sys.getswitchinterval()
            try:
                sys.setswitchinterval(1.0)
                before = provider.frames
                deadline = time.perf_counter() + 0.35
                while time.perf_counter() < deadline:
                    pass
                advanced = provider.frames - before
            finally:
                sys.setswitchinterval(previous)
            if provider.error or advanced < 2:
                raise RuntimeError(f"Native production failed: {provider.error}; frames advanced={advanced}")
            print(f"In-process GPU provider: {advanced} frames published while Python held the GIL")
            return

        status = Label("C++ render thread in this Python process · Ctrl+Q exits", height=2)

        def event(e):
            if e.kind == "metrics":
                status.set_text(f"In-process C++ → shared GPU → native compositor\n"
                                f"Source to Present return: {e.data['frame_age_ms']:.2f} ms")
            elif e.kind == "error":
                status.set_text(str(e.data))
            return False

        with App(VBox(status, Panel(OverlayCanvas(source, on_event=event), "In-process provider"))) as app:
            if args.smoke_seconds:
                app.call_later(args.smoke_seconds, app.exit)
            app.run()
        if provider.error:
            raise RuntimeError(provider.error)
    finally:
        # App's context exits first: the compositor must release its texture leases.
        if source is not None:
            source.close()
        provider.close()


if __name__ == "__main__":
    main()
