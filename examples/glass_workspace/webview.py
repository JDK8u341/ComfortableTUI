"""Two independent browser documents inside native glass cards."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from comfortable_tui import (App, BrowserService, GlassBackground, GlassCard, GlassCardGroup,
                             GlassControl, GlassTheme, GlassWebView, GlassWorkspace, Label, Log, Panel, VBox)


@contextmanager
def local_site():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def make_cards(service, origin, render_mode="gpu", on_browser_event=None):
    pages, views, cards = [], [], []
    for name in ("Notes", "Inbox"):
        page = service.open(origin + "/webview-card.html?name=" + name, origins=[origin],
                            publish_topics=["glass.messages", "glass.telemetry"],
                            subscribe_topics=["glass.messages", "glass.commands"], message_group="glass-demo",
                            width=352, height=300, render_mode=render_mode)
        view = GlassWebView("browser", page, label="Enter/click to interact · Escape to leave",
                            height=300, on_browser_event=on_browser_event)
        pages.append(page)
        views.append(view)
        cards.append(GlassCard(name.lower(), name, [view], width=400,
                               background=GlassBackground("liquid" if name == "Notes" else "frosted")))
    navigation = GlassCard("navigation", "Keyboard navigation", [
        GlassControl("first", "button", "First native control"),
        GlassControl("second", "button", "Second native control")], width=280)
    return pages, views, [navigation, GlassCardGroup("browsers", cards, layout="row")]


def main(*, include_canvas=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--preview", action="store_true", help="Own-window fixture, not a terminal")
    parser.add_argument("--inspectable-windows", action="store_true")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path)
    if include_canvas:
        parser.add_argument("--gpu-source", help="Optional existing FrameProvider channel instead of the CPU demo")
    args = parser.parse_args()
    if args.inspectable_windows and not args.preview:
        parser.error("--inspectable-windows requires --preview")
    report = dict(result="running", browser_events=[])
    log = Log(max_lines=64)
    log.write("Each card has its own document. Notes travel through the scoped browser bridge.")
    with local_site() as origin, BrowserService(executable=args.browser) as service:
        def browser_event(event):
            if event["event"] in ("message", "load_error", "crashed"):
                report["browser_events"].append(event)
                del report["browser_events"][:-64]
                if event["event"] == "message" and event["data"].get("topic") == "glass.messages":
                    log.write(json.dumps(event["data"]["payload"], ensure_ascii=False))
        pages, views, children = make_cards(service, origin, args.render_mode, browser_event)
        image_source = None
        if include_canvas:
            from .mixed import make_canvas
            image_source, image_card = make_canvas(args.gpu_source, log)
            children.append(image_card)
        try:
            def event(e):
                if e.kind == "close_request":
                    if args.preview:
                        owner.close()
                    else:
                        app.exit()
            with GlassWorkspace(children, theme=GlassTheme("liquid"), on_event=event) as workspace:
                if args.preview:
                    from PySide6.QtCore import QTimer
                    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
                    qt = QApplication.instance() or QApplication([])
                    owner = QWidget()
                    owner.setWindowTitle("Comfortable Glass / WebView test owner")
                    owner.resize(360, 240)
                    QVBoxLayout(owner).addWidget(QLabel("Own browser fixture\nTab crosses cards; arrows on headers\nEnter/click page; Escape leaves page\nCtrl+Q exits"))
                    owner.show()
                    workspace.start(int(owner.winId()), inspectable_windows=args.inspectable_windows)
                    errors = []
                    def tick():
                        try:
                            workspace.poll()
                        except Exception as error:
                            errors.append(error)
                            owner.close()
                    timer = QTimer()
                    timer.timeout.connect(tick)
                    timer.start(10)
                    if args.smoke_seconds:
                        QTimer.singleShot(round(args.smoke_seconds * 1000), owner.close)
                    qt.exec()
                    timer.stop()
                    if errors:
                        raise errors[0]
                else:
                    with App(VBox(Label("GLASS / BROWSER CARDS", height=1),
                                  Panel(log, "Messages"),
                                  Label("F6 cards · Tab traverse · Enter webpage · Escape leave · Ctrl+Q quit", height=1))) as app:
                        workspace.bind(app)
                        app.bind("f6", lambda: workspace.command("focus_card"))
                        if args.smoke_seconds:
                            app.call_later(args.smoke_seconds, app.exit)
                        app.run()
                report.update(result="passed", metrics=workspace.metrics(), render_mode=args.render_mode,
                              frame_stats=[page.frame_stats() for page in pages])
        except Exception as error:
            report.update(result="failed", error=repr(error))
            raise
        finally:
            if image_source is not None:
                image_source.close()
            for page in pages:
                page.close()
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
