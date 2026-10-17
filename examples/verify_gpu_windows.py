"""Real Windows GPU test, independent of terminal foreground activation.

Briefly shows our own small test layer. Reads only opaque test-pattern pixels.
Requires overlay extra, native extension, C++ SDK executables and a local GPU.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path, default=Path(".research/gpu-build/Release"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("requires Windows")
    from PIL import ImageGrab
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from comfortable_tui import FrameBuffer, GpuFrameSource, PixelRect, Rect
    from comfortable_tui._overlay_qt import _make_layer_class
    from comfortable_tui._win32_host import Win32
    app = QApplication([])
    if app.platformName() != "windows":
        parser.error("requires QT_QPA_PLATFORM=windows")
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    provider = subprocess.Popen([str((args.bin / "ctui_gpu_producer.exe").resolve()), "--fps", "240", "--pattern", "test"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
    layer, hwnd = None, None
    try:
        handshake = provider.stdout.readline().split()
        if not handshake:
            raise RuntimeError(provider.stderr.read())
        source = GpuFrameSource.attach(handshake[1])
        events = []
        layer = _make_layer_class()(events.append, native=True)
        layer.api = api
        hwnd = int(layer.winId())
        box = Rect(2, 2, 36, 16)
        spec = {"id": 1, "token": "first", "buffer": source.info, "placement": (box, box), "fit": "stretch",
                "smooth": False, "input_mode": "capture", "enabled": True, "focused": False,
                "ime": {"cursor": (0, 0, 1, 1), "text": "", "position": 0, "anchor": 0}, "fps": 120}
        state = {"grid": (40, 20), "items": [spec]}
        viewport = PixelRect(24, 24, 400, 200)
        layer.apply(state, viewport)
        layer.show()
        # Test only: place our small layer above existing windows without input
        # injection or activation. The product never uses global topmost.
        api.user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)

        def pump(seconds):
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                app.processEvents()
                time.sleep(.002)

        pump(.6)
        before = layer.renderer.metrics()
        assert not before["error"] and before["presents"] > 5, before
        native_rect = wintypes.RECT()
        api.user.GetWindowRect(hwnd, ctypes.byref(native_rect))
        colors = []
        expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for i in range(4):
            x = native_rect.left + 20 + 90 + (i % 2) * 180
            y = native_rect.top + 20 + 40 + (i // 2) * 80
            pixel = ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).getpixel((0, 0))[:3]
            colors.append(pixel)
            assert all(abs(a - b) <= 5 for a, b in zip(pixel, expected[i])), (pixel, expected[i])
        api.user.WindowFromPoint.argtypes = [wintypes.POINT]
        api.user.WindowFromPoint.restype = wintypes.HWND
        assert api.user.WindowFromPoint(wintypes.POINT(native_rect.left + 5, native_rect.top + 5)) != hwnd
        assert api.user.WindowFromPoint(wintypes.POINT(native_rect.left + 50, native_rect.top + 50)) == hwnd
        children = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        callback = callback_type(lambda child, _: children.append(child) or True)
        api.user.EnumChildWindows(hwnd, callback, 0)
        assert not children, children
        QTest.mouseClick(layer.items[1], Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        assert any(e.kind == "mouse_down" and e.data["canvas_id"] == 1 for e in events)
        before = layer.renderer.metrics()  # Immediately before the GIL hold, after screenshot/input work.
        interval = sys.getswitchinterval()
        try:
            sys.setswitchinterval(1)
            deadline = time.perf_counter() + .35
            while time.perf_counter() < deadline:
                pass  # Hold Python's GIL; C++ must still consume and present.
        finally:
            sys.setswitchinterval(interval)
        after = layer.renderer.metrics()
        assert after["presents"] > before["presents"] + 5
        assert after["views"][0]["sequence"] > before["views"][0]["sequence"] + 5
        layer.setVisible(False)
        pump(.1)
        paused = layer.renderer.metrics()["presents"]
        pump(.15)
        assert layer.renderer.metrics()["presents"] == paused
        layer.setVisible(True)
        pump(.15)
        assert layer.renderer.metrics()["presents"] > paused
        # A second view aliases the same channel; still one native consumer/window.
        second = dict(spec, id=2, token="second", placement=(Rect(2, 2, 18, 16), Rect(2, 2, 18, 16)))
        layer.apply({"grid": (40, 20), "items": [spec, second]}, viewport)
        pump(.1)
        assert len(layer.renderer.metrics()["views"]) == 2
        layer.apply({"grid": (40, 20), "items": [], "retired": [{"id": 1, "token": "first"}, {"id": 2, "token": "second"}]}, viewport)
        assert len([e for e in events if e.kind == "detached"]) == 2
        subprocess.run([str((args.bin / "ctui_frames_test.exe").resolve()), "--child", source.info.name], check=True,
                       timeout=10, creationflags=0x08000000)
        with FrameBuffer(256, 256, pixel_format="bgra8_premultiplied") as background:
            background.submit(bytes([0, 0, 0, 255]) * (256 * 256))
            for size in (2, 256):
                # Both upsampling (2px) and downsampling (256px)
                # must premultiply before interpolating invisible blue pixels.
                with FrameBuffer(size, size, pixel_format="rgba8") as rgba:
                    rgba.submit((bytes([255, 0, 0, 128]) * (size // 2) + bytes([0, 0, 255, 0]) * (size // 2)) * size)
                    area = (Rect(0, 0, 64, 64), Rect(0, 0, 64, 64))
                    bottom = dict(spec, id=10, token="black", buffer=background.info, placement=area, smooth=True)
                    top = dict(spec, id=11, token="alpha", buffer=rgba.info, placement=area, smooth=True)
                    layer.apply({"grid": (64, 64), "items": [bottom, top]}, PixelRect(24, 24, 64, 64))
                    pump(.15)
                    r = wintypes.RECT()
                    api.user.GetWindowRect(hwnd, ctypes.byref(r))
                    image = ImageGrab.grab(bbox=(r.left + 2, r.top + 2, r.left + 62, r.top + 62))
                    assert max(image.getchannel("B").getextrema()) <= 5, "straight-alpha filtering leaked invisible blue"
                    assert abs(image.getpixel((5, 20))[0] - 128) <= 5, "premultiplied alpha was applied incorrectly"
                    layer.apply({"grid": (64, 64), "items": []}, PixelRect(24, 24, 64, 64))
        layer.apply(state, viewport)
        pump(.15)
        provider.terminate()
        provider.wait(timeout=5)
        pump(.3)
        assert layer.renderer.metrics()["views"][0]["closed"], "provider exit was not reported"
        report = {"result": "passed", "dpi": layer.devicePixelRatioF(), "opaque_test_pixels": colors,
                  "native_children": len(children), "before_gil": before, "after_gil": after,
                  "checks": ["actual_DWM_pattern", "transparent_input_hole", "canvas_input_region", "no_native_children",
                             "per_view_mouse", "GIL_independent_frames", "hide_pause_resume", "shared_source_views", "detached_releases_consumer",
                             "CPU_bridge", "GPU_straight_alpha_filter", "CPU_alpha_formats", "provider_exit"]}
        output = json.dumps(report, indent=2)
        print(output)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
    finally:
        if layer:
            if hwnd:
                api.user.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            layer.shutdown()
            app.processEvents()
        if provider.poll() is None:
            provider.terminate()
        provider.communicate(timeout=5)


if __name__ == "__main__":
    main()
