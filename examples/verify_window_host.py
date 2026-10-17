"""Real Linux C++ host verification: run once per --platform x11/wayland.

Only local fixture pages are loaded. Test input is sent to our own native Qt
widgets; no global keyboard/mouse events or external windows are controlled.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

from comfortable_tui import App, BrowserService, HBox, Label, Panel, Tabs, WebView, WindowHost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=("x11", "wayland"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--data-root", type=Path, help="Keep CEF test data and logs")
    args = parser.parse_args()
    handler = partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parent / "web"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    result = {"requested_platform": args.platform, "checks": []}
    try:
        with BrowserService("verify.window" if args.data_root else None,
                            data_root=args.data_root, display_platform=args.platform) as service:
            page = service.open(origin + "/demo.html", origins=[origin])
            view = WebView(page, fit="stretch")
            mirror = WebView(page, fit="contain")
            tabs = Tabs({"Web": HBox(Panel(view, title="Document"), Panel(mirror, title="Mirror")),
                         "Hidden": Label("Both logical WebViews are hidden")})
            with App(tabs) as app, WindowHost(app, display_platform=args.platform, test_controls=True) as host:
                result["capabilities"] = host.capabilities

                def wait(condition, seconds=15):
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        host.step()
                        if condition():
                            return
                        time.sleep(.02)
                    raise TimeoutError("Native window verification timed out")

                wait(lambda: page.loaded and host.metrics()["source_paints"] > 0)
                metrics = host.metrics()
                assert metrics["views"] == 2 and metrics["sources"] == 1
                assert (metrics["platform"] == "xcb" if args.platform == "x11" else
                        metrics["platform"].startswith("wayland"))
                assert host._rpc.request("test_snapshot")["sampled_colors"] > 10
                result["checks"].append("native_window_paint_and_source_deduplication")
                page.evaluate("document.querySelector('#editor').focus()")
                view.enter_interaction()
                host.step()
                host._rpc.request("test_input", view=str(id(view)), kind="commit", text="原生输入")
                wait(lambda: page.evaluate("document.querySelector('#editor').value") == "原生输入")
                result["checks"].append("native_ime_commit_to_browser")
                page.evaluate("window.clicks=0;document.addEventListener('click',()=>clicks++)")
                host._rpc.request("test_input", view=str(id(view)), kind="click", x=20, y=20)
                wait(lambda: page.evaluate("clicks") > 0)
                result["checks"].append("native_pointer_to_browser")
                host._rpc.request("test_input", view=str(id(view)), kind="key", key=0x01000001, text="\t")
                host.step()
                assert app.focused is mirror and not view.interacting
                # One more key may reach the former native surface before the
                # helper has completely returned keyboard focus to the TUI.
                host._rpc.request("test_input", view=str(id(view)), kind="key", key=0x01000001, text="\t")
                host.step()
                assert app.focused is tabs and not mirror.interacting
                result["checks"].append("native_tab_returns_to_tui")
                before = host.metrics()["source_paints"]
                old_interval = sys.getswitchinterval()
                try:
                    sys.setswitchinterval(1000)
                    until = time.perf_counter() + .4
                    while time.perf_counter() < until:
                        pass
                finally:
                    sys.setswitchinterval(old_interval)
                after = host.metrics()["source_paints"]
                assert after > before
                result["gil_pause_paints"] = after - before
                result["checks"].append("native_frames_continue_during_python_gil_pause")
                tabs.select(1)
                for _ in range(100):
                    host.step(dt=.05)
                wait(lambda: not host._last["items"][0]["visible"])
                assert not any(item["visible"] for item in host._last["items"])
                result["checks"].append("tabs_hide_both_views")
                tabs.select(0)
                for _ in range(100):
                    host.step(dt=.05)
                wait(lambda: all(item["visible"] for item in host._last["items"]))
                result["checks"].append("tabs_restore_both_views")
                page.resize(640, 360)
                host.step()
                wait(lambda: page.source.sequence > 0)
                assert host.metrics()["sources"] == 1
                result["checks"].append("native_buffer_replacement")
                host._rpc.request("resize", width=900, height=600)
                wait(lambda: host.geometry["width"] == 900)
                assert view.transform.source_size == (640, 360)
                result["checks"].append("window_resize_and_transform")
                result["metrics"] = host.metrics()
                host.close()
                assert view._session is None and mirror._session is None
                result["checks"].append("consumer_detach_before_frame_owner_close")
    finally:
        server.shutdown()
        server.server_close()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
