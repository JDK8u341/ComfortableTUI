"""Optional CEF WebViews: isolated accounts, messages and embedded widget views.

Build instructions: docs/webview.md. Windows uses the terminal's native overlay;
Linux uses an owned C++ window (--platform x11 or --platform wayland).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from comfortable_tui import (App, BrowserService, Button, DataTable, HBox, Label, ListView,
                             Panel, Tabs, Text, VBox, WebView, WindowHost, gpu_available)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("x11", "wayland"))
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.name == "nt" and (not gpu_available() or os.environ.get("COMFORTABLE_TUI_OVERLAY_BACKEND") == "qt"):
        parser.error("This example requires the native compositor; build COMFORTABLE_TUI_BUILD_GPU=1 (docs/webview.md)")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(Path(__file__).parent / "web"), **kw)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    events = []

    def record(kind, data):
        events.append(dict(kind=kind, data=data))
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(events, indent=2, ensure_ascii=False,
                default=lambda v: asdict(v) if is_dataclass(v) else str(v)), encoding="utf-8")

    try:
        with BrowserService("org.comfortabletui.examples.webview", display_platform=args.platform) as browser:
            record("browser_service", browser.capabilities)
            options = dict(origins=[origin], publish_topics=["selection.changed"],
                           subscribe_topics=["selection.changed"], message_group="demo-link")
            first = browser.open(origin + "/demo.html", profile=browser.profile("account-a", persistent=True), **options)
            second = browser.open(origin + "/demo.html", profile=browser.profile("account-b"), **options)
            status = Label("Separate cookies · shared authorized topic · F6: TUI focus · Ctrl+Q: quit", height=1)

            def browser_event(e):
                if e["event"] == "message":
                    status.set_text("Message: " + json.dumps(e["data"], ensure_ascii=False))

            def view(page, **kw):
                def canvas_event(e):
                    if e.kind not in ("mouse_move", "terminal_mouse"):
                        record(e.kind, e.data)
                    if e.kind == "close_request":
                        app.exit()
                    return False
                return WebView(page, on_event=canvas_event, on_browser_event=browser_event, **kw)

            direct = HBox(Panel(view(first), "Account A · persistent"), Panel(view(second), "Account B · temporary"), gap=1)
            listing = ListView([f"Row {i:04}" for i in range(1000)], item_height=5,
                item_widget=lambda i, value: HBox(Label(value, width=12), view(first), gap=1))
            table = DataTable(["Item", "Web mirror"], [(str(i), "") for i in range(100)], item_height=5,
                column_widths=[8, 30], cell_widget=lambda r, c, v: view(second) if c == 1 else None)
            embedded = VBox(HBox(Panel(listing, "Virtual list"), Panel(table, "Data table"), gap=1),
                Panel(Text("Inline page: ", view(first, width=32, height=6), " · same document"), "Text", height=8))
            tabs = Tabs({"Web": direct, "Embedded": embedded, "Hidden": Label("Browser views are hidden here")})
            root = VBox(tabs, status, HBox(Button("Next tab", lambda: tabs.select(tabs.selected+1)),
                Button("Publish from Python", lambda: browser.publish("selection.changed", {"item_id": 88},
                    message_group="demo-link")), Button("Quit", lambda: app.exit()), height=1))
            with App(root) as app:
                if args.smoke_seconds:
                    app.call_later(8, lambda: tabs.select(2))
                    app.call_later(9.5, lambda: record("checkpoint", {"phase": "hidden", "items": len(app._overlay_placements)}))
                    app.call_later(10, lambda: tabs.select(0))
                    app.call_later(13, lambda: app.show_modal(Panel(Label("Underlying browser views hidden"), "Modal", height=6)))
                    app.call_later(14, lambda: record("checkpoint", {"phase": "modal", "items": len(app._overlay_placements)}))
                    app.call_later(15, app.close_modal)
                    app.call_later(args.smoke_seconds, app.exit)
                if os.name == "nt":
                    app.run()
                else:
                    with WindowHost(app, display_platform=args.platform) as host:
                        record("window_host", host.capabilities)
                        host.run()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
