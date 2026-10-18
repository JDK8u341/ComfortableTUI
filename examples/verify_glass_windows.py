"""Real-desktop integration fixture; operates only on its own native test windows.

This is not a terminal emulator. Use examples.glass_workspace for real-terminal binding.
"""
from __future__ import annotations

import argparse
import ctypes
import faulthandler
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

from comfortable_tui import FrameBuffer, GlassCard, GlassControl, GlassTheme, GlassWorkspace, GpuFrameSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-provider", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--focus-timeout", type=float, default=12,
                        help="Allow time to activate this test fixture when Windows denies foreground requests")
    args = parser.parse_args()
    faulthandler.dump_traceback_later(max(30, args.focus_timeout + 20))
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from shiboken6 import isValid
    from comfortable_tui._win32_host import Win32
    app = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass verification fixture")
    owner.resize(450, 350)
    owner.move(480, 180)
    owner.show()
    events, report = [], {"checks": []}
    def check(name):
        report["checks"].append(name)
        print("PASS " + name, flush=True)

    provider = source = workspace = None
    try:
        with FrameBuffer(160, 80, pixel_format="bgra8_premultiplied") as frames, FrameBuffer(160, 80) as replacement:
            frames.submit(bytes((70, 180, 120, 255)) * 160 * 80)
            replacement.submit(bytes((120, 40, 180, 255)) * 160 * 80)
            source = frames
            if args.gpu_provider:
                provider = subprocess.Popen([str(args.gpu_provider.resolve())], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)
                line = provider.stdout.readline().split()
                assert len(line) == 6 and line[0] == "CTUI_GPU_V1"
                source = GpuFrameSource.attach(line[1])
            canvas = GlassControl("canvas", "canvas", "Frame input", source=source)
            controls = [GlassControl("action", "button", "Action"), GlassControl("toggle", "toggle", "Enabled"), canvas]
            cards = [GlassCard("main", "Verification", controls, width=280),
                     GlassCard("side", "Other card", [GlassControl("input", "input", "Notes")], width=260)]
            workspace = GlassWorkspace(cards, theme=GlassTheme(), on_event=events.append).start(int(owner.winId()))

            def pump(seconds):
                end = time.perf_counter() + seconds
                while time.perf_counter() < end:
                    app.processEvents()
                    workspace.poll()
                    time.sleep(.004)

            def wait(callback, timeout=12):
                end = time.perf_counter() + timeout
                while time.perf_counter() < end:
                    pump(.015)
                    value = callback()
                    if value:
                        return value
                raise TimeoutError(f"glass verification condition timed out: foreground={api.user.GetForegroundWindow()}, metrics={workspace.metrics()}")

            wait(lambda: workspace.metrics().get("applied", 0) >= 1 and len(workspace.metrics()["windows"]) == 2)
            wait(lambda: workspace.metrics().get("canvas_frames", 0) >= (3 if args.gpu_provider else 1))
            check("native_ui_and_external_frames_presented")
            pump(.6)
            before = workspace.metrics()["frames"]
            pump(.3)
            report["idle_ui_draws_in_300ms"] = workspace.metrics()["frames"] - before
            assert report["idle_ui_draws_in_300ms"] <= 2
            report["initial_metrics"] = workspace.metrics()
            check("settled_ui_does_not_redraw_continuously")
            hwnd = workspace.metrics()["windows"][0]["hwnd"]
            dpi = api.user.GetDpiForWindow(hwnd) / 96

            def mouse(message, x, y, button=0):
                point = round(y * dpi) << 16 | round(x * dpi)
                api.user.SendMessageW(hwnd, message, button, point)

            mouse(0x201, 50, 121, 1)
            # Simulate a model update between down and up: capture and focus
            # must survive the control-plane scene acknowledgement.
            workspace.update()
            pump(.08)
            mouse(0x202, 50, 121)
            wait(lambda: any(e.kind == "click" and e.control == "action" for e in events))
            assert sum(e.kind == "click" and e.control == "action" for e in events) == 1
            check("capture_release_preserves_exactly_one_click")
            stale_scene = workspace.snapshot()
            mouse(0x201, 50, 165, 1)
            mouse(0x202, 50, 165)
            wait(lambda: controls[1].value == 1)
            check("native_control_change_reaches_python")
            stale_serial = workspace._native.configure(stale_scene)
            wait(lambda: workspace.metrics()["applied"] >= stale_serial)
            mouse(0x201, 50, 165, 1)
            mouse(0x202, 50, 165)
            wait(lambda: controls[1].value == 0)
            check("stale_scene_cannot_erase_unacknowledged_native_edit")
            mouse(0x201, 140, 264, 1)
            mouse(0x202, 140, 264)
            click = wait(lambda: next((e for e in events if e.kind == "mouse_down" and e.control == "canvas"), None))
            assert abs(click.position[0] - source.info.width / 2) < 2
            assert abs(click.position[1] - source.info.height / 2) < 2
            assert any(e.kind == "mouse_up" and e.control == "canvas" for e in events)
            canvas.enabled = False
            workspace.update()
            workspace.poll()
            wait(lambda: not workspace._pending)
            before_events = sum(e.control == "canvas" for e in events)
            mouse(0x200, 140, 264)
            mouse(0x201, 140, 264, 1)
            mouse(0x202, 140, 264)
            api.user.SendMessageW(hwnd, 0x101, 65, 0)
            pump(.1)
            assert sum(e.control == "canvas" for e in events) == before_events
            canvas.enabled = True
            workspace.update()
            check("canvas_source_coordinates_and_mouse_pair")
            workspace.configure(layout="focus")
            workspace.poll()
            poses = workspace.metrics()["windows"]
            interval = sys.getswitchinterval()
            try:
                sys.setswitchinterval(2)
                until = time.perf_counter() + .45
                while time.perf_counter() < until:
                    pass
            finally:
                sys.setswitchinterval(interval)
            moved = workspace.metrics()["windows"]
            report["gil_held_seconds"] = .45
            report["motion_during_gil_pixels"] = max(abs(a["rect"][0] - b["rect"][0]) for a, b in zip(poses, moved))
            assert any(abs(a["rect"][0] - b["rect"][0]) > 10 for a, b in zip(poses, moved))
            check("native_motion_continues_while_python_holds_gil")
            pump(.4)
            workspace.configure(layout="ribbon")
            workspace.poll()
            pump(.5)
            workspace.configure(layout="deck")
            workspace.poll()
            pump(.5)
            check("interruptible_orbit_focus_ribbon_deck_layouts")
            workspace.configure(theme=GlassTheme("solid", reduced_motion=True, reduced_transparency=True))
            workspace.poll()
            wait(lambda: workspace.metrics()["material"] == "solid")
            check("opaque_and_reduced_motion_preferences")
            canvas.source = replacement
            workspace.update()
            workspace.poll()
            wait(lambda: workspace._attached_refs == [replacement] and not workspace._pending)
            assert workspace.metrics()["windows"][0]["hwnd"] == hwnd
            check("canvas_replacement_ack_preserves_native_window")
            old_owner = wintypes.RECT()
            api.user.GetWindowRect(int(owner.winId()), ctypes.byref(old_owner))
            old_card = workspace.metrics()["windows"][0]["rect"]
            owner.move(owner.x() + 80, owner.y() + 20)
            pump(.08)
            new_owner = wintypes.RECT()
            api.user.GetWindowRect(int(owner.winId()), ctypes.byref(new_owner))
            new_card = workspace.metrics()["windows"][0]["rect"]
            report["translation_error_pixels"] = [abs(new_card[0] - old_card[0] - (new_owner.left - old_owner.left)),
                                                   abs(new_card[1] - old_card[1] - (new_owner.top - old_owner.top))]
            assert abs(new_card[0] - old_card[0] - (new_owner.left - old_owner.left)) <= 2
            assert abs(new_card[1] - old_card[1] - (new_owner.top - old_owner.top)) <= 2
            check("native_owner_translation_follows_without_python_geometry")
            owner.showMaximized()
            wait(lambda: workspace.metrics()["compact"])
            for window in workspace.metrics()["windows"]:
                x, y, width, height = window["rect"]
                assert 0 <= x and 0 <= y
                assert x + width <= api.user.GetSystemMetrics(0) and y + height <= api.user.GetSystemMetrics(1)
            owner.showNormal()
            pump(.2)
            check("maximized_owner_uses_bounded_compact_deck")
            workspace.configure(backdrop="dim")
            workspace.poll()
            owner.activateWindow()
            api.user.SetForegroundWindow(int(owner.winId()))
            workspace.command("focus_card")
            print("FOCUS fixture if Windows foreground permission was denied", flush=True)
            wait(lambda: workspace.metrics()["active"] and workspace.metrics()["backdrop_visible"], timeout=args.focus_timeout)
            pump(.3)  # Drain the asynchronous focus_card command before changing groups.
            other = QWidget()
            other.setWindowTitle("Comfortable Glass separate focus fixture")
            other.show()
            pump(.15)
            other.activateWindow()
            api.user.SetForegroundWindow(int(other.winId()))
            print("FOCUS separate fixture", flush=True)
            wait(lambda: not workspace.metrics()["active"] and not workspace.metrics()["backdrop_visible"], timeout=args.focus_timeout)
            assert api.user.IsWindowVisible(hwnd)
            workspace.command("focus_card")
            wait(lambda: workspace.metrics()["active"] and workspace.metrics()["backdrop_visible"])
            other.close()
            api.user.SendMessageW(hwnd, 0x100, 0x1B, 0)  # Escape is handled before focused control keys.
            wait(lambda: api.user.GetForegroundWindow() == int(owner.winId()))
            workspace.configure(backdrop="none")
            workspace.poll()
            wait(lambda: not workspace.metrics()["backdrop_visible"])
            check("background_follows_focus_group_cards_survive_inactivity_and_escape")
            workspace.hide()
            workspace.poll()
            wait(lambda: not api.user.IsWindowVisible(hwnd))
            workspace.show()
            workspace.poll()
            wait(lambda: api.user.IsWindowVisible(hwnd))
            check("dismiss_and_recall")
            owner.showMinimized()
            wait(lambda: not api.user.IsWindowVisible(hwnd))
            owner.showNormal()
            wait(lambda: api.user.IsWindowVisible(hwnd))
            check("owner_minimize_restore")
            report["metrics"] = workspace.metrics()
            old_windows = [w["hwnd"] for w in report["metrics"]["windows"]]
            owner.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            owner.close()
            wait(lambda: workspace._closed)
            assert not any(api.user.IsWindow(window) for window in old_windows)
            check("owner_destroy_closes_windows_and_consumers")
            report["source"] = "shared_gpu" if args.gpu_provider else "cpu_framebuffer"
            report["result"] = "passed"
    except Exception as error:
        report.update(result="failed", error=str(error))
        print("FAIL " + repr(error), flush=True)
        raise
    finally:
        if workspace:
            workspace.close()
        if isinstance(source, GpuFrameSource):
            source.close()
        if provider:
            provider.stdin.close()
            try:
                provider.wait(timeout=3)
            except subprocess.TimeoutExpired:
                provider.terminate()
                provider.wait(timeout=3)
            provider.stdout.close()
        if isValid(owner):
            owner.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        faulthandler.cancel_dump_traceback_later()
        print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, indent=2))


if __name__ == "__main__":
    main()
