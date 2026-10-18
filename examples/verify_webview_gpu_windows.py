"""Real CEF/D3D11 GPU transport verification; only reads our opaque fixture window.

Shows a small test window briefly. No external sites, accounts or global input injection.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--benchmark-seconds", type=float, default=0,
                        help="Also compare CPU/GPU transport of the same animated page at two resolutions")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("requires Windows D3D11")
    from PIL import ImageChops, ImageGrab
    from PySide6.QtWidgets import QApplication
    from comfortable_tui import BrowserPolicy, BrowserService, GpuFrameSource, PixelRect, Rect
    from comfortable_tui._overlay_qt import _make_layer_class
    from comfortable_tui._win32_host import Win32

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(Path(__file__).parent / "web"), **kw)

        def log_message(self, *_):
            pass

    app = QApplication([])
    if app.platformName() != "windows":
        parser.error("requires QT_QPA_PLATFORM=windows")
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    result = dict(result="failed", checks=[])
    layer = None
    try:
        with BrowserService(executable=args.browser, policy=BrowserPolicy(allow_evaluate=True)) as service:
            errors = []
            service.on_event(lambda e: errors.append(e) if e["event"] in ("error", "crashed") else None)
            page = service.open(origin + "/gpu.html", origins=[origin], width=640, height=360,
                                render_mode="gpu", fps=60)
            assert isinstance(page.source, GpuFrameSource)
            result["negotiated"] = page.info["frame_backend"]
            events = []
            layer = _make_layer_class()(events.append, native=True)
            layer.api = api
            hwnd = int(layer.winId())
            viewport = PixelRect(40, 40, 640, 360)
            box = Rect(0, 0, 80, 24)

            def spec(source, ident=1):
                return dict(id=ident, token=str(ident), buffer=source.info, placement=(box, box),
                            fit="stretch", smooth=False, input_mode="capture", enabled=True,
                            focused=False, ime={"cursor": (0, 0, 1, 1), "text": "", "position": 0, "anchor": 0}, fps=120)

            def apply(source, mirror=False):
                items = [spec(source)]
                if mirror:
                    items.append(dict(spec(source, 2), placement=(Rect(40, 0, 40, 24), Rect(40, 0, 40, 24))))
                layer.apply(dict(grid=(80, 24), items=items), viewport)

            def pump(seconds):
                deadline = time.perf_counter() + seconds
                while time.perf_counter() < deadline:
                    app.processEvents()
                    service.poll()
                    assert not errors, errors
                    time.sleep(.002)

            def wait(predicate, seconds=10):
                deadline = time.perf_counter() + seconds
                while not predicate():
                    if time.perf_counter() > deadline:
                        raise TimeoutError(str(page.frame_stats()))
                    pump(.02)

            apply(page.source)
            layer.show()
            # Test-only topmost; the product retains ordinary terminal ownership.
            api.user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            wait(lambda: page.loaded and page.frame_stats().get("published_frames", 0) >= 8)
            pump(.15)
            stats = page.frame_stats()
            native = layer.renderer.metrics()
            assert stats["cpu_paints"] == 0 and stats["accelerated_paints"] > 0
            assert native["views"][0]["gpu_shared"] and not native["error"]
            result["checks"].append("CEF_accelerated_paint_to_existing_GPU_transport")

            def pixel(x, y):
                r = wintypes.RECT()
                api.user.GetWindowRect(hwnd, ctypes.byref(r))
                return ImageGrab.grab(bbox=(r.left + x, r.top + y, r.left + x + 1, r.top + y + 1)).getpixel((0, 0))[:3]

            red, background = pixel(50, 40), pixel(600, 40)
            assert max(abs(a - b) for a, b in zip(red, (255, 64, 0))) <= 6, red
            assert max(abs(a - b) for a, b in zip(background, (17, 34, 51))) <= 6, background
            result["fixture_pixels"] = [red, background]
            result["checks"].append("actual_DWM_fixture_pixels")
            before = layer.renderer.metrics()
            interval = sys.getswitchinterval()
            try:
                sys.setswitchinterval(1)
                deadline = time.perf_counter() + .35
                while time.perf_counter() < deadline:
                    pass
            finally:
                sys.setswitchinterval(interval)
            after = layer.renderer.metrics()
            assert after["views"][0]["sequence"] > before["views"][0]["sequence"] + 3
            result["checks"].append("GIL_independent_browser_frames_and_present")

            page.input("focus_in")
            window = wintypes.RECT()
            api.user.GetWindowRect(hwnd, ctypes.byref(window))
            before_menu = ImageGrab.grab(bbox=(window.left, window.top, window.left + 640, window.top + 360)).convert("RGB")
            rect = page.evaluate("(()=>{let r=document.querySelector('select').getBoundingClientRect();return {x:r.x+10,y:r.y+10}})()")
            rect = {key: round(value) for key, value in rect.items()}
            page.input("mouse_down", **rect, button="left", buttons=1, clicks=1)
            page.input("mouse_up", **rect, button="left", buttons=0, clicks=1)
            wait(lambda: page.frame_stats()["popup_paints"] > 0)
            pump(.1)
            popup_stats = page.frame_stats()
            popup = popup_stats["popup"]
            assert popup["visible"]
            bounds = (max(0, popup["x"]), max(0, popup["y"]),
                      min(640, popup["x"] + popup["width"]), min(360, popup["y"] + popup["height"]))
            menu_image = ImageGrab.grab(bbox=(window.left + bounds[0], window.top + bounds[1],
                                            window.left + bounds[2], window.top + bounds[3])).convert("RGB")
            changed = ImageChops.difference(before_menu.crop(bounds), menu_image)
            raw = changed.tobytes()
            assert sum(max(raw[i:i + 3]) > 10 for i in range(0, len(raw), 3)) > 100
            result["popup_stats"] = popup_stats
            page.input("key_down", key_code=40)
            page.input("key_up", key_code=40)
            page.input("key_down", key_code=13)
            page.input("key_up", key_code=13)
            wait(lambda: page.evaluate("document.querySelector('select').value") == "Banana")
            result["checks"].append("native_select_popup_GPU_composition_and_input")
            apply(page.source, mirror=True)
            pump(.2)
            assert len(layer.renderer.metrics()["views"]) == 2
            result["checks"].append("one_GPU_source_multiple_views")
            layer.hide()
            pump(.1)
            before_hidden = layer.renderer.metrics()["presents"]
            pump(.15)
            assert layer.renderer.metrics()["presents"] == before_hidden
            layer.show()
            pump(.1)
            result["checks"].append("hide_and_resume")
            # Keep the old channel until native scene replacement is acknowledged.
            old = page.source
            # Synthetic consumer token pins the old frame just as WebView does.
            page._display_buffer("verification", old.info)
            for w, h in ((800, 450), (320, 180), (640, 360)):
                page.resize(w, h)
                assert not old._closed
                apply(page.source)
                page._display_buffer("verification", page.source.info)
                assert old._closed
                old = page.source
                wait(lambda: page.frame_stats()["published_frames"] > 3)
            result["checks"].append("resize_and_retired_channel_ACK_lifetime")
            result["frame_stats"] = page.frame_stats()
            result["display_stats"] = layer.renderer.metrics()
            page.close()
            assert not old._closed
            layer.apply(dict(grid=(80, 24), items=[]), viewport)
            page._display_detached("verification")
            assert old._closed
            assert service._rpc.request("frame_resources")["gpu_channels"] == 0
            result["checks"].append("page_close_retains_until_native_detach")
            if args.benchmark_seconds:
                if not 1 <= args.benchmark_seconds <= 30:
                    raise ValueError("benchmark-seconds must be 1..30")
                samples = []
                for width, height in ((640, 360), (1920, 1080)):
                    for mode in ("cpu", "gpu"):
                        page = service.open(origin + "/gpu.html", width=width, height=height,
                                            render_mode=mode, fps=60)
                        page._display_buffer("benchmark", page.source.info)
                        apply(page.source)
                        wait(lambda: page.loaded and layer.renderer.metrics()["views"][0]["sequence"] > 5)
                        pump(.25)
                        native_before, browser_before = layer.renderer.metrics(), page.frame_stats()
                        timings = []
                        started = time.perf_counter()
                        while time.perf_counter() - started < args.benchmark_seconds:
                            pump(.02)
                            timings.append(layer.renderer.metrics()["submit_ms"])
                        elapsed = time.perf_counter() - started
                        native_after, browser_after = layer.renderer.metrics(), page.frame_stats()
                        assert not browser_after["error"] and not native_after["error"]
                        field = "cpu_paints" if mode == "cpu" else "accelerated_paints"
                        paints = browser_after[field] - browser_before[field]
                        assert paints > 10
                        samples.append(dict(mode=mode, source_size=[width, height], display_size=[640, 360],
                            seconds=elapsed, browser_callbacks=paints,
                            presents=native_after["presents"] - native_before["presents"],
                            published_sequences=native_after["views"][0]["sequence"] - native_before["views"][0]["sequence"],
                            submit_ms_p50=statistics.median(timings),
                            submit_ms_p95=sorted(timings)[min(len(timings) - 1, int(len(timings) * .95))],
                            GPU_copy_wait_ms_mean=((browser_after["copy_wait_ms"] - browser_before["copy_wait_ms"]) / paints
                                                   if mode == "gpu" else None)))
                        page.close()
                        layer.apply(dict(grid=(80, 24), items=[]), viewport)
                        page._display_detached("benchmark")
                result["benchmark"] = samples
                result["checks"].append("matched_CPU_GPU_animated_page_samples")
            layer.shutdown()
            layer = None
            result["result"] = "passed"
    except BaseException as error:
        result["error"] = str(error)
        raise
    finally:
        if layer:
            layer.shutdown()
        server.shutdown()
        server.server_close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
