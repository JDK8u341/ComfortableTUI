"""Run a short real Windows Terminal test of native positioning, z-order and IPC.

Requires wt.exe, a local interactive Windows desktop, and the overlay extra.
Creates its own terminal window and closes it through the example's callback.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from comfortable_tui._win32_host import Win32


def wait_for(callback, *, timeout=12, message="condition not reached"):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(.025)
    raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="keep a JSON result report")
    args = parser.parse_args()
    if os.name != "nt" or shutil.which("wt.exe") is None:
        parser.error("requires Windows Terminal (wt.exe) on a local Windows desktop")
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    hwnd = host = None
    with tempfile.TemporaryDirectory(prefix="comfortable-tui-overlay-") as folder:
        path = Path(folder) / "events.json"
        example = Path(__file__).with_name("overlay_canvas.py").resolve()

        def load():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []

        def ready():
            report = load()
            if any(e["kind"] == "error" for e in report):
                raise RuntimeError(str([e for e in report if e["kind"] == "error"]))
            if any(e["kind"] == "transform" for e in report):
                return next(e["data"] for e in report if e["kind"] == "ready")

        subprocess.Popen(["wt.exe", "-w", "new", "new-tab", "--title", "Overlay test " + uuid.uuid4().hex[:8],
                          "-d", str(example.parent.parent), sys.executable, str(example),
                          "--smoke-seconds", "20", "--report", str(path)], creationflags=0x08000000)
        try:
            result = wait_for(ready, message="native canvas did not become ready")
            hwnd, host = result["hwnd"], result["host_hwnd"]
            assert api.user.GetWindow(hwnd, 4) == host, "Qt reset native owner"
            original = wintypes.RECT()
            api.user.GetWindowRect(host, ctypes.byref(original))
            api.user.SetWindowPos(host, None, original.left + 20, original.top + 10,
                                  max(500, original.right - original.left - 80),
                                  max(400, original.bottom - original.top - 40), 0x0010 | 0x0200)

            def aligned():
                transforms = [e["data"] for e in load() if e["kind"] == "transform"]
                if len(transforms) < 2:
                    return False
                rect = wintypes.RECT()
                api.user.GetWindowRect(hwnd, ctypes.byref(rect))
                expected = transforms[-1]["clip_rect"]
                actual = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                target = (expected["x"], expected["y"], expected["width"], expected["height"])
                return rect if all(abs(a - b) <= 1 for a, b in zip(actual, target)) else False

            rect = wait_for(aligned, timeout=4, message="window and physical canvas rectangle differ")
            above = api.user.GetWindow(host, 3)
            for _ in range(1024):
                if not above or above == hwnd:
                    break
                above = api.user.GetWindow(above, 3)
            assert above == hwnd, "canvas is below the terminal"
            assert api.user.GetWindow(hwnd, 4) == host, "owner lost after resize"
            x, y = (rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2
            point = (y << 16) | x
            # Targeted messages to our own helper, never global keyboard/mouse injection.
            api.user.SendMessageW(hwnd, 0x201, 1, point)
            api.user.SendMessageW(hwnd, 0x202, 0, point)
            api.user.SendMessageW(hwnd, 0x100, 0x27, 0)
            api.user.SendMessageW(hwnd, 0x101, 0x27, 0)
            wait_for(lambda: {"mouse_down", "mouse_up", "key_down", "key_up"}.issubset(
                {e["kind"] for e in load()}), timeout=4, message="native input did not return to provider")
            api.user.SetForegroundWindow(host)
            time.sleep(.2)
            assert api.user.IsWindowVisible(hwnd), "canvas hid when terminal regained foreground"
            wait_for(lambda: any(e["kind"] == "metrics" and e["data"]["sequence"] > 0 for e in load()),
                     timeout=4, message="no shared frames painted")
            events = load()
            report = {"result": "passed", "host": result,
                      "checks": ["owner", "z_order_after_host_raise", "move_resize_physical_alignment",
                                 "native_mouse_keyboard", "shared_frames_painted"],
                      "transform": [e["data"] for e in events if e["kind"] == "transform"][-1],
                      "metrics": [e["data"] for e in events if e["kind"] == "metrics"][-1]}
            print(json.dumps(report, ensure_ascii=True, indent=2))
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            if hwnd and api.user.IsWindow(hwnd):
                api.user.SendMessageW(hwnd, 0x10, 0, 0)
                wait_for(lambda: not api.user.IsWindow(hwnd), timeout=5, message="helper did not close")
            # Auto-exit is a second cleanup path if startup fails before ready.
            if host and api.user.IsWindow(host):
                wait_for(lambda: not api.user.IsWindow(host), timeout=5, message="test terminal did not exit")


if __name__ == "__main__":
    main()
