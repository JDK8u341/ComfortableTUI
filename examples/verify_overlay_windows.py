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


def activate_test_terminal(api, hwnd):
    """Foreground restrictions can leave a programmatically opened WT in back."""
    api.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.user.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    api.kernel.GetCurrentThreadId.restype = wintypes.DWORD
    own_thread = api.kernel.GetCurrentThreadId()
    foreground_thread = api.user.GetWindowThreadProcessId(api.user.GetForegroundWindow(), None)
    attached = own_thread != foreground_thread and api.user.AttachThreadInput(own_thread, foreground_thread, True)
    try:
        api.user.SetForegroundWindow(hwnd)
        if api.user.GetForegroundWindow() != hwnd:
            # This verifier owns the test terminal; the library never forces
            # an unrelated background terminal into the foreground.
            api.user.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
            api.user.SwitchToThisWindow(hwnd, True)
    finally:
        if attached:
            api.user.AttachThreadInput(own_thread, foreground_thread, False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="keep a JSON result report")
    parser.add_argument("--embedded", action="store_true", help="test many canvases, virtual rows, tabs and modal")
    parser.add_argument("--webview", action="store_true", help="test the optional native CEF browser example")
    args = parser.parse_args()
    if os.name != "nt" or shutil.which("wt.exe") is None:
        parser.error("requires Windows Terminal (wt.exe) on a local Windows desktop")
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    hwnd = host = None
    success = False
    with tempfile.TemporaryDirectory(prefix="comfortable-tui-overlay-") as folder:
        path = Path(folder) / "events.json"
        example = Path(__file__).with_name("webview.py" if args.webview else
                                           "embedded_canvas.py" if args.embedded else "overlay_canvas.py").resolve()

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
                          "--smoke-seconds", "24" if args.webview else "20", "--report", str(path)], creationflags=0x08000000)
        try:
            result = wait_for(ready, message="native canvas did not become ready")
            hwnd, host = result["hwnd"], result["host_hwnd"]
            def visible():
                if api.user.IsWindowVisible(hwnd):
                    return True
                activate_test_terminal(api, host)
                return False

            wait_for(visible, timeout=4, message="test terminal did not become foreground")
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
                expected = transforms[-1]["layer_rect"]
                actual = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                target = (expected["x"], expected["y"], expected["width"], expected["height"])
                return rect if all(abs(a - b) <= 1 for a, b in zip(actual, target)) else False

            rect = wait_for(aligned, timeout=4, message="window and physical canvas rectangle differ")
            api.user.WindowFromPoint.argtypes = [wintypes.POINT]
            api.user.WindowFromPoint.restype = wintypes.HWND
            hole = api.user.WindowFromPoint(wintypes.POINT(rect.left + 5, rect.top + 5))
            assert api.user.GetAncestor(hole, 2) == host, "transparent TUI area intercepts native input"
            children = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            callback = callback_type(lambda child, _: children.append(child) or True)
            api.user.EnumChildWindows(hwnd, callback, 0)
            assert not children, "logical canvases unexpectedly created native child windows"
            def above_terminal():
                if not visible():
                    return False
                above = api.user.GetWindow(host, 3)
                for _ in range(1024):
                    if not above or above == hwnd:
                        break
                    above = api.user.GetWindow(above, 3)
                return above == hwnd
            wait_for(above_terminal, timeout=4, message="canvas is below the terminal")
            assert api.user.GetWindow(hwnd, 4) == host, "owner lost after resize"
            clip = [e["data"]["clip_rect"] for e in load() if e["kind"] == "transform"][-1]
            x = round(clip["x"] + clip["width"] / 2 - rect.left)
            y = round(clip["y"] + clip["height"] / 2 - rect.top)
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
            if args.embedded or args.webview:
                wait_for(lambda: any(e["kind"] == "checkpoint" and e["data"]["phase"] == "hidden" for e in load()),
                         timeout=9, message="hidden-page checkpoint missing")
                hidden = [e["data"] for e in load() if e["kind"] == "checkpoint" and e["data"]["phase"] == "hidden"][-1]
                assert hidden["items"] == 0 and not api.user.IsWindowVisible(hwnd), "layer remains over inactive page"
                wait_for(lambda: any(e["kind"] == "checkpoint" and e["data"]["phase"] == "modal" for e in load()),
                         timeout=6, message="modal checkpoint missing")
                modal = [e["data"] for e in load() if e["kind"] == "checkpoint" and e["data"]["phase"] == "modal"][-1]
                assert modal["items"] == 0 and not api.user.IsWindowVisible(hwnd), "layer covers TUI modal"
                wait_for(visible, timeout=6, message="layer did not resume")
                ready_events = [e["data"] for e in load() if e["kind"] == "ready"]
                assert len(ready_events) >= (2 if args.webview else 3)
                assert len({(e["pid"], e["hwnd"]) for e in ready_events}) == 1, "multiple native canvas windows"
            events = load()
            report = {"result": "passed", "host": result,
                      "checks": ["owner", "z_order_after_host_raise", "move_resize_physical_alignment",
                                 "native_mouse_keyboard", "shared_frames_painted", "transparent_input_hole", "no_native_children"],
                      "transform": [e["data"] for e in events if e["kind"] == "transform"][-1],
                      "metrics": [e["data"] for e in events if e["kind"] == "metrics"][-1]}
            if args.embedded:
                report["checks"] += ["many_canvases_one_hwnd", "virtual_row_disposal", "tab_hide", "modal_hide", "resume"]
                report["checkpoints"] = [e["data"] for e in events if e["kind"] == "checkpoint"]
                report["logical_canvases_created"] = len(ready_events)
            if args.webview:
                report["checks"] += ["cef_native_cpu_frames", "two_webviews_one_hwnd", "tab_hide", "modal_hide", "resume"]
            print(json.dumps(report, ensure_ascii=True, indent=2))
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            success = True
        finally:
            if args.report and not success:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps({"result": "incomplete", "events": load(),
                    "foreground": api.user.GetForegroundWindow()}, ensure_ascii=False, indent=2), encoding="utf-8")
            if hwnd and api.user.IsWindow(hwnd):
                api.user.SendMessageW(hwnd, 0x10, 0, 0)
                wait_for(lambda: not api.user.IsWindow(hwnd), timeout=5, message="helper did not close")
            # Auto-exit is a second cleanup path if startup fails before ready.
            if host and api.user.IsWindow(host):
                wait_for(lambda: not api.user.IsWindow(host), timeout=5, message="test terminal did not exit")


if __name__ == "__main__":
    main()
