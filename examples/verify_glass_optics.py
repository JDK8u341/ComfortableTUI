"""Own-window desktop checks for optical materials, element events and CardGroup.

Activate the optical background, then the verification owner if Windows denies
foreground requests. No foreign windows or terminal input are automated.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import faulthandler
import json
from pathlib import Path
import time

from comfortable_tui import GlassBackground, GlassCard, GlassCardGroup, GlassControl, GlassTheme, GlassWorkspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--focus-timeout", type=float, default=90)
    parser.add_argument("--hold", type=float, default=0, help="Keep the successful own-window scene for visual checks")
    parser.add_argument("--manual-start", action="store_true", help="Wait for the own-window Start checks button")
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
    from glass_workspace.fixtures import OpticalTestBackground
    from comfortable_tui._win32_host import Win32
    app = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    api.user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    world = OpticalTestBackground()
    world.showMaximized()
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / Routing verification owner")
    owner.setStyleSheet("QWidget {background:#15243a;color:white;}")
    owner.resize(300, 180)
    owner.move(500, 280)
    owner_layout = QVBoxLayout(owner)
    owner_layout.addWidget(QLabel("OWN-WINDOW TEST HOST\n\nClear / frost / event routing\nNot a terminal emulator"))
    start_button = QPushButton("Start checks")
    start_button.setProperty("ready", not args.manual_start)
    start_button.clicked.connect(lambda: start_button.setProperty("ready", True))
    if args.manual_start:
        owner_layout.addWidget(start_button)
    owner.show()
    events, report = [], dict(checks=[], result="running")
    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    faulthandler.dump_traceback_later(args.focus_timeout + args.hold + 60)
    workspace = None
    try:
        clear = GlassCard("clear", "A clear lens", [GlassControl("note", "input", "Your thought…")],
                          subtitle="Header events choose the drag behavior.", width=260, background=GlassBackground(blur=0, opacity=.025))
        frost = GlassCard("frost", "Soft focus", [GlassControl("status", "text", "Independent background", text="Actual Gaussian blur")],
                          width=260, background=GlassBackground("frosted", blur=20, opacity=.06))
        fixed = GlassCard("fixed", "Pinned by policy", [GlassControl("status", "text", "Drag policy", text="draggable=False")],
                          width=250, background=GlassBackground("solid", tint=0x16243A, opacity=1), draggable=False)
        group = GlassCardGroup("pair", [clear, frost], layout="row", gap=16)
        workspace = GlassWorkspace([group, fixed], theme=GlassTheme(reduced_motion=True), on_event=events.append)
        workspace.start(int(owner.winId()), inspectable_windows=True)

        def pump(seconds):
            end = time.perf_counter() + seconds
            while time.perf_counter() < end:
                app.processEvents()
                workspace.poll()
                time.sleep(.004)

        def wait(callback, timeout=10):
            end = time.perf_counter() + timeout
            while time.perf_counter() < end:
                pump(.02)
                value = callback()
                if value:
                    return value
            raise TimeoutError(str(workspace.metrics()))

        def check(name):
            report["checks"].append(name)
            report["metrics"] = workspace.metrics()
            save()
            print("PASS " + name, flush=True)

        def windows():
            return {w["id"]: w for w in workspace.metrics().get("windows", [])}

        def submit():
            applied = workspace.metrics()["applied"]
            workspace.update()
            wait(lambda: workspace.metrics()["applied"] > applied)

        wait(lambda: len(windows()) == 3)
        print("FOCUS own optical background, then Routing verification owner", flush=True)
        wait(lambda: start_button.property("ready"), timeout=args.focus_timeout)
        wait(lambda: workspace.metrics()["active"], timeout=args.focus_timeout)
        wait(lambda: workspace.metrics()["capture_frames"] > 0 and windows()["clear"]["material"] == "liquid")
        assert windows()["frost"]["material"] == "frosted" and windows()["fixed"]["material"] == "solid"
        check("per_card_liquid_frosted_solid_rendering")
        a, b = windows()["clear"]["rect"], windows()["frost"]["rect"]
        assert b[0] > a[0] + a[2] - 2 and abs(a[1] - b[1]) <= 2
        check("native_row_group_layout")
        pump(4)  # Let focus transitions and external input visualizers settle.
        before = workspace.metrics()
        pump(.7)
        after = workspace.metrics()
        report["idle_700ms"] = {key: after[key] - before[key] for key in ("frames", "optical_frames", "capture_frames", "unchanged_capture_frames")}
        assert report["idle_700ms"]["optical_frames"] <= 4, report["idle_700ms"]
        check("self_capture_echo_does_not_sustain_optical_repainting")

        def mouse(hwnd, message, x, y, button=0):
            p = wintypes.POINT(round(x), round(y))
            api.user.ScreenToClient(hwnd, ctypes.byref(p))
            api.user.SendMessageW(hwnd, message, button, (p.y & 0xffff) << 16 | (p.x & 0xffff))

        def drag(card, allowed):
            item = windows()[card.id]
            x, y, _, _ = item["rect"]
            dpi = api.user.GetDpiForWindow(item["hwnd"]) / 96
            sx, sy = x + 45 * dpi, y + 55 * dpi
            mouse(item["hwnd"], 0x201, sx, sy, 1)
            pump(.12)  # Card receives the event and chooses its default behavior.
            mouse(item["hwnd"], 0x200, sx - 65, sy + 35, 1)
            pump(.12)
            mouse(item["hwnd"], 0x202, sx - 65, sy + 35)
            pump(.12)
            if allowed:
                # Metrics are sampled every 500ms, not on each pointer event.
                def moved():
                    current = windows()[card.id]
                    return (current["pinned"] and abs(current["rect"][0] - x + 65) <= 2
                            and abs(current["rect"][1] - y - 35) <= 2)
                wait(moved)
            else:
                current = windows()[card.id]
                assert abs(current["rect"][0] - x) <= 2 and abs(current["rect"][1] - y) <= 2

        drag(fixed, False)
        check("card_draggable_false_prevents_default_behavior")
        group.on_preview_event = lambda e: e.kind == "pointer_down"
        drag(clear, False)
        check("group_preview_consumption_prevents_drag")
        group.on_preview_event = None
        clear.on_event = lambda e: e.kind == "pointer_down"
        drag(clear, False)
        check("card_handler_consumption_prevents_drag")
        clear.on_event = None
        drag(clear, True)
        position = windows()["clear"]["rect"][:2]
        workspace.update()
        pump(.3)
        assert windows()["clear"]["rect"][:2] == position
        assert any(e.kind == "drag_end" for e in events)
        check("element_owned_drag_survives_scene_updates")
        workspace.command("reflow")
        wait(lambda: not windows()["clear"]["pinned"])
        check("explicit_reflow_restores_container_layout")
        group.visible = False
        workspace.update()
        wait(lambda: not api.user.IsWindowVisible(windows()["clear"]["hwnd"]))
        assert not api.user.IsWindowVisible(windows()["frost"]["hwnd"]) and api.user.IsWindowVisible(windows()["fixed"]["hwnd"])
        group.visible = True
        workspace.update()
        wait(lambda: api.user.IsWindowVisible(windows()["clear"]["hwnd"]))
        check("group_visibility_is_inherited_without_hiding_other_roots")
        group.enabled = False
        workspace.update()
        pump(.15)
        drag(clear, False)
        group.enabled = True
        submit()
        check("group_disabled_prevents_child_pointer_behavior")
        # Disable, hide, or revoke the policy while a header owns a pointer.
        # No mouse-up is sent until after the model change: capture must cancel.
        for mutation in ("disable", "hide", "policy"):
            item = windows()[clear.id]
            x, y, _, _ = item["rect"]
            dpi = api.user.GetDpiForWindow(item["hwnd"]) / 96
            sx, sy = x + 45 * dpi, y + 55 * dpi
            mouse(item["hwnd"], 0x201, sx, sy, 1)
            pump(.12)
            mouse(item["hwnd"], 0x200, sx - 30, sy + 15, 1)
            pump(.12)
            before_cancel = len([e for e in events if e.kind == "drag_cancel"])
            if mutation == "disable":
                group.enabled = False
            elif mutation == "hide":
                group.visible = False
            else:
                clear.draggable = False
            submit()
            wait(lambda: len([e for e in events if e.kind == "drag_cancel"]) > before_cancel)
            group.enabled = group.visible = clear.draggable = True
            submit()
            pump(.15)
            mouse(item["hwnd"], 0x202, sx - 30, sy + 15)
            pump(.08)
            wait(lambda: not windows()[clear.id]["pinned"])
        check("in_flight_drag_cancels_on_disable_hide_or_policy_change")
        clear.background = GlassBackground("frosted", blur=16, opacity=.07)
        workspace.update()
        wait(lambda: windows()["clear"]["material"] == "frosted")
        assert windows()["fixed"]["material"] == "solid"
        clear.background = GlassBackground(blur=0, opacity=.025)
        workspace.update()
        wait(lambda: windows()["clear"]["material"] == "liquid")
        check("one_card_background_changes_independently")
        owner.activateWindow()
        api.user.SetForegroundWindow(int(owner.winId()))
        workspace.configure(backdrop="frosted")
        workspace.poll()
        wait(lambda: workspace.metrics()["backdrop_material"] == "frosted")
        check("workspace_frosted_background_uses_optical_blur")
        workspace.configure(backdrop="dim")
        workspace.poll()
        wait(lambda: workspace.metrics()["backdrop_material"] == "dim")
        workspace.configure(backdrop="none")
        check("dim_is_reported_as_dimming_not_frosted")
        report.update(result="passed", metrics=workspace.metrics())
        save()
        pump(args.hold)
    except Exception as error:
        report.update(result="failed", error=str(error))
        raise
    finally:
        if workspace:
            workspace.close()
        owner.close()
        world.close()
        faulthandler.cancel_dump_traceback_later()
        save()
        print(json.dumps({k: v for k, v in report.items() if k != "metrics"}, indent=2))


if __name__ == "__main__":
    main()
