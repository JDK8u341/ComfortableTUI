"""Windows: many canvases in one layer, virtual rows, table cells, inline text and tabs.

Install .[overlay], then run python examples/embedded_canvas.py.
One external producer supplies all views; replace it with independent sources if needed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import multiprocessing as mp
from pathlib import Path
import queue

from comfortable_tui import (App, Button, DataTable, FrameBuffer, HBox, Label, ListView,
                             OverlayCanvas, Panel, Tabs, Text, VBox)
from overlay_canvas import render_frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    context = mp.get_context("spawn")
    stop, controls = context.Event(), context.Queue(maxsize=64)
    diagnostics = []

    def serialize(value):
        return asdict(value) if is_dataclass(value) else str(value)

    def record(kind, data):
        diagnostics.append({"kind": kind, "data": data})
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(diagnostics, default=serialize, indent=2), encoding="utf-8")

    with FrameBuffer(320, 180, pixel_format="bgra8_premultiplied") as frames:
        status = Label("Tab / F6: focus · scroll rows · click an image · Ctrl+Q: exit", height=1)

        def view(name, **kwargs):
            def event(e):
                if e.kind not in ("mouse_move", "terminal_mouse"):
                    record(e.kind, dict(e.data, view=name))
                if e.kind == "mouse_down":
                    status.set_text(f"{name}: {e.data['source_position']}")
                    try:
                        controls.put_nowait({"cursor": e.data["source_position"]})
                    except queue.Full:
                        pass
                elif e.kind == "close_request":
                    app.exit()
                # False lets arrows bubble to the containing list/table.
                return False
            return OverlayCanvas(frames, on_event=event, fps=60, **kwargs)

        listing = ListView((f"Track {i:04}" for i in range(1000)), item_height=5,
                           item_widget=lambda i, text: HBox(Label(text, width=13), view(f"list:{i}"), gap=1))
        table = DataTable(["Service", "Live frame"], [(f"Source {i}", "") for i in range(100)],
                          item_height=4, column_widths=[12, 22],
                          cell_widget=lambda r, c, value: view(f"table:{r}") if c == 1 else None)
        inline = Text("Inline video: ", view("text:A", width=18, height=4),
                      "  another: ", view("text:B", width=12, height=3), "\nText wraps around widget boxes.")
        page = VBox(HBox(Panel(listing, "Virtual list · 1,000 rows"),
                         Panel(table, "Data table · scroll horizontally"), gap=1),
                    Panel(inline, "Mixed text", height=7), gap=1)
        tabs = Tabs({"Embedded": page, "Other": Label("The images are hidden on this page."),
                     "Large": Panel(view("large"), "Same source, independent view")})

        def modal():
            app.show_modal(Panel(VBox(Label("All underlying canvases are hidden."),
                                     Button("Close", lambda: app.close_modal())), "Modal", height=6))

        root = VBox(Label("Shared overlay layer · one HWND, many logical canvases", height=1), tabs, status,
                    HBox(Button("Next tab", lambda: tabs.select(tabs.selected + 1)),
                         Button("Scroll list", lambda: listing.select(listing.selected + 10)),
                         Button("Modal", modal), Button("Quit", lambda: app.exit()), height=1), gap=1)
        producer = context.Process(target=render_frames, args=(frames.info, controls, stop, 60))
        try:
            producer.start()
            with App(root) as app:
                if args.smoke_seconds:
                    # Exercise row disposal, clipping, page suspension and modal occlusion.
                    app.call_later(3, lambda: listing.select(40))
                    app.call_later(4, lambda: record("checkpoint", {"phase": "scrolled", "items": len(app._overlay_placements)}))
                    app.call_later(5, lambda: tabs.select(1))
                    app.call_later(6, lambda: record("checkpoint", {"phase": "hidden", "items": len(app._overlay_placements)}))
                    app.call_later(7, lambda: tabs.select(0))
                    app.call_later(9, modal)
                    app.call_later(10, lambda: record("checkpoint", {"phase": "modal", "items": len(app._overlay_placements)}))
                    app.call_later(11, app.close_modal)
                    app.call_later(args.smoke_seconds, app.exit)
                app.run()
        except Exception as exc:
            record("error", {"message": str(exc)})
            raise
        finally:
            stop.set()
            producer.join(timeout=3)
            if producer.is_alive():
                producer.terminate()
                producer.join(timeout=3)
            controls.close()
            controls.cancel_join_thread()


if __name__ == "__main__":
    mp.freeze_support()
    main()
