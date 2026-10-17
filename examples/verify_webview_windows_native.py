"""CEF + real DComp layer without relying on Windows Terminal foreground policy.

Briefly shows our own small test layer, like verify_gpu_windows.py. This is a
controlled display test, not proof of arbitrary-terminal following/z-order.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("requires Windows")
    from PIL import ImageGrab
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from comfortable_tui import App, BrowserService, HBox, PixelRect, WebView
    from comfortable_tui._overlay_qt import _make_layer_class
    from comfortable_tui._win32_host import Win32

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(Path(__file__).parent / "web"), **kw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    qt, api, layer, hwnd = QApplication([]), Win32(), None, None
    events, result = [], {"mode": "controlled_dcomp_layer", "checks": []}
    try:
        with BrowserService() as browser:
            page = browser.open(origin + "/demo.html", width=640, height=360, origins=[origin])
            views = [WebView(page, fit="stretch"), WebView(page, fit="stretch")]
            with App(HBox(*views)) as tui:
                frame = tui.render(40, 20)

                def emit(event):
                    events.append(event)
                    key = event.data.get("canvas_id")
                    for view in views:
                        if key == id(view):
                            view._native_event(event)

                layer = _make_layer_class()(emit, native=True)
                layer.api = api
                hwnd = int(layer.winId())

                def configure():
                    items = []
                    for view in views:
                        _, full, clip = frame.overlays[id(view)]
                        items.append(dict(id=id(view), token=view._event_token, buffer=page.source.info,
                            placement=(full, clip), fit=view.fit, smooth=False, input_mode="capture",
                            enabled=True, focused=False, ime=view.ime, fps=120))
                        view._session = layer
                    layer.apply(dict(grid=(40, 20), items=items), PixelRect(24, 24, 480, 240))

                def pump(seconds):
                    deadline = time.perf_counter() + seconds
                    while time.perf_counter() < deadline:
                        qt.processEvents()
                        browser.poll()
                        time.sleep(.002)

                configure()
                layer.show()
                # Test only: our own small window, no global input injection.
                api.user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
                pump(.5)
                deadline = time.perf_counter() + 10
                while not page.loaded and time.perf_counter() < deadline:
                    pump(.02)
                assert page.loaded
                page.evaluate("document.documentElement.innerHTML=`<head><style>html,body{margin:0;background:#f00;}"
                    "i{position:absolute;top:0;width:5px;height:5px;background:#00f;animation:m .4s linear infinite alternate}"
                    "@keyframes m{from{left:0}to{left:100px}}</style></head><body><input id=editor><i></i></body>`;"
                    "window.clicks=0;document.addEventListener('click',()=>clicks++);true")
                pump(.3)
                before = layer.renderer.metrics()
                assert not before["error"] and before["presents"] > 0
                assert len(before["views"]) == 2 and not any(v["gpu_shared"] for v in before["views"])
                result["checks"].append("cef_cpu_frames_to_native_dcomp_mirrors")
                native = wintypes.RECT()
                api.user.GetWindowRect(hwnd, ctypes.byref(native))
                pixel = ImageGrab.grab(bbox=(native.left+100, native.top+150,
                                             native.left+101, native.top+151)).getpixel((0, 0))[:3]
                assert all(abs(a-b) <= 5 for a, b in zip(pixel, (255, 0, 0))), pixel
                result["sampled_test_pixel"] = pixel
                result["checks"].append("actual_dwm_browser_test_pixel")
                page.evaluate("document.querySelector('#editor').focus()")
                event = QInputMethodEvent()
                event.setCommitString("中文原生")
                QApplication.sendEvent(layer.items[id(views[0])], event)
                pump(.1)
                assert page.evaluate("document.querySelector('#editor').value") == "中文原生"
                result["checks"].append("native_ime_event_to_cef")
                QTest.mouseClick(layer.items[id(views[0])], Qt.MouseButton.LeftButton, pos=QPoint(30, 80))
                pump(.1)
                assert page.evaluate("clicks") > 0
                result["checks"].append("native_mouse_to_cef")
                before = layer.renderer.metrics()
                interval = sys.getswitchinterval()
                try:
                    sys.setswitchinterval(1)
                    deadline = time.perf_counter() + .4
                    while time.perf_counter() < deadline:
                        pass
                finally:
                    sys.setswitchinterval(interval)
                after = layer.renderer.metrics()
                assert after["presents"] > before["presents"] + 2
                result["checks"].append("gil_independent_native_browser_frames")
                layer.setVisible(False)
                pump(.05)
                paused = layer.renderer.metrics()["presents"]
                pump(.1)
                assert layer.renderer.metrics()["presents"] == paused
                layer.setVisible(True)
                pump(.2)
                assert layer.renderer.metrics()["presents"] > paused
                result["checks"].append("native_hide_pause_and_resume")
                page.resize(800, 450)
                configure()
                pump(.2)
                assert len(layer.renderer.metrics()["views"]) == 2
                result["checks"].append("native_source_replacement")
                layer.apply(dict(grid=(40, 20), items=[], retired=[dict(id=id(v), token=v._event_token) for v in views]),
                            PixelRect(24, 24, 480, 240))
                assert sum(e.kind == "detached" for e in events) == 2
                for view in views:
                    view._session = None
                result["checks"].append("native_consumer_detach")
                result["metrics"] = after
                result["result"] = "passed"
    finally:
        if layer:
            if hwnd:
                api.user.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            layer.shutdown()
            qt.processEvents()
        server.shutdown()
        server.server_close()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
