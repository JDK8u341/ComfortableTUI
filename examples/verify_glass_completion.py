"""API-driven regression for native motion, stateful controls and local layouts."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

from comfortable_tui import (FrameBuffer, GlassCanvas, GlassCard, GlassContainer, GlassControl, GlassEffect,
                             GlassMotion, GlassTheme, GlassWorkspace, register_glass_motions, register_glass_shader)
from examples.glass_workspace.controls import load_extension


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, default=Path(".build/glass-controls"))
    parser.add_argument("--report", type=Path, default=Path(".build/glass-completion.json"))
    parser.add_argument("--webview", action="store_true")
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    extension = load_extension(next(args.extension.glob("ctui_glass_controls*.pyd")) if args.extension.is_dir() else args.extension)
    register_glass_motions(extension)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    qt = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / extension completion verification")
    owner.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    owner.resize(500, 300)
    owner.show()
    qt.processEvents()
    a = GlassControl("a", "example.dial", "Independent A", value=.2, motion=GlassMotion("example.ease"))
    b = GlassControl("b", "example.dial", "Independent B", value=.8)
    card = GlassCard("stateful", "Native callbacks", [a, b], motion=GlassMotion("example.ease"))
    workspace = GlassWorkspace([card], theme=GlassTheme("solid"))
    report = dict(result="running", checks=[], fixture="owned_QWidget")
    resources = ExitStack()

    def pump(seconds):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            qt.processEvents()
            workspace.poll()
            time.sleep(.004)

    def wait(predicate, timeout=8):
        deadline = time.perf_counter()+timeout
        while time.perf_counter() < deadline:
            pump(.02)
            if predicate():
                return
        raise TimeoutError(str(workspace.metrics()))

    try:
        workspace.start(int(owner.winId()))
        wait(lambda: extension.instance_stats()["created"] == 2 and
             extension.instance_stats()["motion_frames"] > 0 and workspace.metrics().get("windows"))
        report["checks"].append("independent_native_control_and_motion_instances")
        hwnd = workspace.metrics()["windows"][0]["hwnd"]
        serial = workspace.metrics()["applied"]
        card.width += 40
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial)
        assert workspace.metrics()["windows"][0]["hwnd"] == hwnd
        assert extension.instance_stats()["created"] == 2
        assert extension.instance_stats()["updated"] >= 4
        report["checks"].append("resize_preserves_hwnd_and_control_private_state")
        serial = workspace.metrics()["applied"]
        workspace.layout = "focus"
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial)
        before = workspace.metrics()["windows"][0]["rect"]
        frames = extension.instance_stats()["motion_frames"]
        interval = sys.getswitchinterval()
        try:
            sys.setswitchinterval(1)
            end = time.perf_counter()+.35
            while time.perf_counter() < end:
                pass
        finally:
            sys.setswitchinterval(interval)
        after = workspace.metrics()["windows"][0]["rect"]
        assert extension.instance_stats()["motion_frames"] > frames
        assert before[:2] != after[:2]
        report["checks"].append("custom_layout_motion_continues_without_python_or_qt_pump")
        deadline = time.perf_counter()+6
        while time.perf_counter() < deadline:
            idle = extension.instance_stats()["motion_frames"]
            pump(.35)
            if extension.instance_stats()["motion_frames"] == idle:
                break
        else:
            raise AssertionError(extension.instance_stats())
        report["checks"].append("settled_native_callbacks_stop")
        serial = workspace.metrics()["applied"]
        before = extension.instance_stats()
        workspace.restart_motion(card)
        wait(lambda: extension.instance_stats()["motion_frames"] > before["motion_frames"])
        assert extension.instance_stats()["motion_created"] == before["motion_created"]
        assert workspace.metrics()["applied"] == serial
        report["checks"].append("native_restart_reuses_instance_without_scene_submission")
        shader = register_glass_shader(Path(__file__).parent/"glass_workspace/shaders/duotone.json")
        b.effects = [GlassEffect(shader, parameters=(.2,)),
                     GlassEffect("glow", target="overlay", parameters=(.5, 6))]
        b.motion = GlassMotion("example.pulse", (8,))
        workspace.update()
        wait(lambda: extension.instance_stats()["effect_writes"] >= 3)
        from comfortable_tui import _native_glass
        diagnostics = _native_glass.effect_diagnostics()
        writes, serial = extension.instance_stats()["effect_writes"], workspace.metrics()["applied"]
        interval = sys.getswitchinterval()
        try:
            sys.setswitchinterval(1)
            end = time.perf_counter()+.2
            while time.perf_counter() < end:
                pass
        finally:
            sys.setswitchinterval(interval)
        assert extension.instance_stats()["effect_writes"] > writes
        current = _native_glass.effect_diagnostics()
        assert current["surface_allocations"] == diagnostics["surface_allocations"]
        assert current["package_shader_compilations"] == diagnostics["package_shader_compilations"] == 1
        assert workspace.metrics()["applied"] == serial
        report["checks"].append("native_effect_animation_without_gil_scene_submission_allocation_or_recompile")
        serial = workspace.metrics()["applied"]
        card.controls[0] = GlassControl("a", "example.dial", "Replacement", value=.4)
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial)
        assert extension.instance_stats()["created"] == 3 and extension.instance_stats()["destroyed"] == 1
        assert b.value == .8
        report["checks"].append("same_id_replacement_releases_only_retired_private_state")

        # Two independently transported canvases in a local tree, alongside buttons.
        first = resources.enter_context(FrameBuffer(128, 96))
        second = resources.enter_context(FrameBuffer(128, 96))
        first.submit(bytes([30, 120, 220, 255])*(128*96))
        second.submit(bytes([220, 80, 30, 255])*(128*96))
        canvas_a, canvas_b = GlassCanvas("canvas-a", first, height=96), GlassCanvas("canvas-b", second, height=96)
        resources.callback(canvas_a.canvas.close)
        resources.callback(canvas_b.canvas.close)
        route = []
        def record(name):
            return lambda e: route.append(name) if e.kind == "click" else None
        left = GlassControl("left", "button", "Left", corner_radius=20, on_event=record("leaf"))
        right = GlassControl("right", "button", "Right")
        buttons = GlassContainer("buttons", [left, right], layout="row", gap=12,
                                 on_preview_event=record("row-preview"), on_event=record("row-bubble"))
        images = GlassContainer("images", [canvas_a, canvas_b], layout="row", gap=12)
        body = GlassContainer("body", [buttons, images], padding=8,
                              on_preview_event=record("root-preview"), on_event=record("root-bubble"))
        card.controls, card.width, card.motion = [body], 600, None
        serial = workspace.metrics()["applied"]
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial and workspace.metrics().get("canvas_frames", 0) >= 2)
        assert workspace.metrics()["windows"][0]["hwnd"] == hwnd
        assert canvas_a._workspace is workspace and canvas_b._workspace is workspace
        assert extension.instance_stats()["created"] == extension.instance_stats()["destroyed"]
        report["checks"].append("nested_layout_hosts_two_independent_canvas_sources_and_preserves_hwnd")

        from comtypes import GUID
        from comtypes.client import GetModule
        accessibility = GetModule("oleacc.dll")
        accessible = ctypes.POINTER(accessibility.IAccessible)()
        get_accessible = ctypes.OleDLL("oleacc").AccessibleObjectFromWindow
        get_accessible.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.POINTER(GUID),
                                  ctypes.POINTER(ctypes.POINTER(accessibility.IAccessible))]
        get_accessible(hwnd, 0xfffffffc, ctypes.byref(accessibility.IAccessible._iid_), ctypes.byref(accessible))
        from comfortable_tui._win32_host import Win32
        api = Win32()
        api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        api.user.SendMessageW.restype = ctypes.c_ssize_t
        assert accessible.accChildCount == 7 and accessible.accRole[1] == 0x14
        x, y, width, height = accessible.accLocation(3)
        rx, ry, _, _ = accessible.accLocation(4)
        assert rx > x+width and ry == y
        rect = workspace.metrics()["windows"][0]["rect"]
        cx, cy = round(x+1-rect[0]), round(y+1-rect[1])
        corner = ((cy & 0xffff) << 16) | (cx & 0xffff)
        api.user.SendMessageW(hwnd, 0x201, 1, corner)
        api.user.SendMessageW(hwnd, 0x202, 0, corner)
        pump(.06)
        assert not route
        report["checks"].append("rounded_node_corner_is_excluded_from_pointer_hit")
        px, py = round(x+width/2-rect[0]), round(y+height/2-rect[1])
        point = ((py & 0xffff) << 16) | (px & 0xffff)
        api.user.SendMessageW(hwnd, 0x201, 1, point)
        api.user.SendMessageW(hwnd, 0x202, 0, point)
        wait(lambda: len(route) >= 5)
        assert route == ["root-preview", "row-preview", "leaf", "row-bubble", "root-bubble"]
        for expected in ("right", "canvas-a", "canvas-b"):
            api.user.SendMessageW(hwnd, 0x100, 9, 0)
            wait(lambda: workspace.metrics()["windows"][0]["focused_control"] == expected)
        report["checks"].append("nested_accessibility_geometry_pointer_route_and_tab_order")
        body.enabled = False
        serial = workspace.metrics()["applied"]
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial)
        assert not workspace.metrics()["windows"][0]["focused_control"]
        assert accessible.accState[6] & 1
        body.visible = False
        serial = workspace.metrics()["applied"]
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial and not canvas_a._view_visible() and not canvas_b._view_visible())
        assert accessible.accState[6] & 0x8000
        assert not first._closed and not second._closed
        body.enabled = body.visible = True
        serial = workspace.metrics()["applied"]
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial and canvas_a._view_visible() and canvas_b._view_visible())
        report["checks"].append("ancestor_hide_disable_cancels_focus_and_preserves_application_sources")
        accessible = None
        if args.webview:
            from comfortable_tui import BrowserService, GlassWebView
            from examples.glass_workspace.webview import local_site
            origin = resources.enter_context(local_site())
            service = resources.enter_context(BrowserService(executable=args.browser))
            previous = canvas_b
            for mode in ("cpu", "gpu"):
                page = service.open(origin+"/webview-card.html?name=Nested", origins=[origin],
                                    width=240, height=96, render_mode=mode)
                resources.callback(page.close)
                browser = GlassWebView("browser", page, height=96, auto_resize=False)
                resources.callback(browser.canvas.close)
                images.children[1] = browser
                workspace.update()
                wait(lambda: page.loaded and browser._workspace is workspace and previous._workspace is None)
                assert page._attachments
                images.children.pop()
                workspace.update()
                wait(lambda: browser._workspace is None and not page._attachments)
                browser.canvas.close()
                page.close()
                images.children.append(canvas_b)
                workspace.update()
                wait(lambda: canvas_b._workspace is workspace)
                previous = canvas_b
                report["checks"].append(mode+"_webview_in_nested_container_uses_existing_canvas_lifecycle")
        workspace.close()
        stats = extension.instance_stats()
        assert stats["created"] == stats["destroyed"] and stats["motion_created"] == stats["motion_destroyed"]
        assert stats["wrong_thread"] == 0
        report.update(result="passed", extension=stats)
    except Exception as error:
        report.update(result="failed", error=repr(error), metrics=workspace.metrics())
        raise
    finally:
        workspace.close()
        resources.close()
        owner.close()
        qt.processEvents()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
