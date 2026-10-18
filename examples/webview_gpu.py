"""Windows GPU WebViews, including mirrors inside ListView, DataTable and Text.

Use --render-mode cpu for the same UI with CPU frame transport. See docs/webview-gpu.md.
"""
from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import threading

from comfortable_tui import (App, BrowserService, DataTable, HBox, Label, ListView,
                             Panel, Tabs, Text, VBox, WebView)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This example targets the Windows native overlay; Linux GPU is not implemented")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(Path(__file__).parent / "web"), **kw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with BrowserService(executable=args.browser) as service:
            page = service.open(origin + "/gpu.html", origins=[origin], publish_topics=["gpu.demo"],
                                render_mode=args.render_mode, width=800, height=450)
            status = Label("Enter/click: interact · Esc: leave · F6: menu · Ctrl+Q: quit", height=1)

            def message(event):
                if event["event"] in ("message", "error"):
                    status.set_text(str(event["data"]))

            page.on_event(message)
            tabs = Tabs({
                "Page": Panel(WebView(page), "GPU texture output" if args.render_mode == "gpu" else "CPU output"),
                "Embedded": HBox(
                    Panel(ListView(["First", "Second"], item_height=7,
                                   item_widget=lambda i, value: WebView(page)), "Virtual list"),
                    Panel(DataTable(["Page"], [("mirror",)], item_height=7,
                                    cell_widget=lambda r, c, value: WebView(page)), "Data table"), gap=1),
                "Inline": Panel(Text("Inline mirror: ", WebView(page, width=50, height=12)), "Text"),
                "Hidden": Label("All browser views are hidden here. One page is mirrored across the other tabs."),
            })
            with App(VBox(tabs, status), fps=120) as app:
                if args.smoke_seconds:
                    app.call_later(args.smoke_seconds, app.exit)
                app.run()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
