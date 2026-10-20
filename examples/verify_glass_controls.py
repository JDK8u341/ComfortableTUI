"""Native control SDK regression; directs messages only to its own windows."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time
import sys

from comfortable_tui import GlassControl, GlassTheme, GlassWorkspace
from examples.glass_workspace.controls import MixerCard, load_extension


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    extension = load_extension(args.extension)
    from PySide6.QtWidgets import QApplication, QWidget
    from comfortable_tui._win32_host import Win32
    qt = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / Native control SDK verification")
    owner.resize(380, 200)
    owner.show()
    first, second = MixerCard("a", "A"), MixerCard("b", "B")
    events = []
    report = dict(result="running", checks=[], host="Qt_fixture")
    workspace = GlassWorkspace([first, second], theme=GlassTheme("solid", reduced_motion=True),
                               on_event=events.append)
    try:
        workspace.start(int(owner.winId()))

        def pump(seconds):
            end = time.perf_counter()+seconds
            while time.perf_counter() < end:
                qt.processEvents()
                workspace.poll()
                time.sleep(.004)

        def wait(condition):
            end = time.perf_counter()+8
            while time.perf_counter() < end:
                pump(.025)
                if condition():
                    return
            raise TimeoutError(str(workspace.metrics()))

        def check(name):
            report["checks"].append(name)
            print("PASS "+name, flush=True)

        wait(lambda: len(workspace.metrics()["windows"]) == 2)
        pump(.3)
        hwnd = next(w["hwnd"] for w in workspace.metrics()["windows"] if w["id"] == "a")
        scale = api.user.GetDpiForWindow(hwnd)/96

        def mouse(message, x, y, flags=0):
            point = ((round(y*scale)&0xffff) << 16) | (round(x*scale)&0xffff)
            api.user.SendMessageW(hwnd, message, flags, point)

        def click(x, y):
            mouse(0x201, x, y, 1)
            mouse(0x202, x, y)
            pump(.08)

        click(150, 148)
        assert first.dial.value != .45 and second.dial.value == .45
        check("native_type_paints_and_updates_only_its_own_instance")
        before = first.dial.value
        click(22, 104)  # Row corner outside the registered circle.
        assert first.dial.value == before
        check("custom_hit_shape_excludes_row_corners")
        workspace.command("select", "a")
        workspace.command("focus_card")
        workspace.command("next_control")
        wait(lambda: any(w["focused_control"] == "gain" for w in workspace.metrics()["windows"]))
        api.user.SendMessageW(hwnd, 0x100, 39, 0)
        wait(lambda: first.dial.value > before)
        assert second.dial.value == .45
        check("registered_control_participates_in_tab_and_directional_input")
        from comtypes import GUID
        from comtypes.client import GetModule
        accessibility = GetModule("oleacc.dll")
        accessible = ctypes.POINTER(accessibility.IAccessible)()
        get_accessible = ctypes.OleDLL("oleacc").AccessibleObjectFromWindow
        get_accessible.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.POINTER(GUID),
                                  ctypes.POINTER(ctypes.POINTER(accessibility.IAccessible))]
        get_accessible(hwnd, 0xfffffffc, ctypes.byref(accessibility.IAccessible._iid_), ctypes.byref(accessible))
        assert accessible.accRole[1] == 0x33
        assert "percent" in accessible.accValue[1]
        assert accessible.accDefaultAction[1] == "Adjust"
        accessible = None
        check("custom_control_publishes_registered_accessibility_role_value_and_action")
        # Painter/input callbacks execute on the native STA while Python sleeps.
        post = ctypes.PyDLL("user32").PostMessageW  # Retain the GIL during dispatch.
        post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        post.restype = wintypes.BOOL
        start = extension.paint_count()
        previous_interval = sys.getswitchinterval()
        try:
            sys.setswitchinterval(1.)
            point = (round(147*scale) << 16) | round(149*scale)
            assert post(hwnd, 0x200, 0, point)
            until = time.perf_counter()+.15
            while time.perf_counter() < until:
                pass
            assert extension.paint_count() > start
        finally:
            sys.setswitchinterval(previous_interval)
        check("native_paint_does_not_require_python_poll_or_gil")
        workspace.poll()
        assert any(e.kind == "pointer_enter" and e.control == "gain" and
                   e.generation == first.dial._generation for e in events)
        check("pointer_hooks_carry_control_instance_generation")
        serial = workspace.metrics()["applied"]
        start = workspace.metrics()["frames"]
        workspace.present(first, first.readout, opacity=.25)
        wait(lambda: workspace.metrics()["frames"] > start)
        assert workspace.metrics()["applied"] == serial
        check("presentation_delta_redraws_without_scene_submission")
        pump(.3)
        start = workspace.metrics()["frames"]
        pump(.35)
        assert workspace.metrics()["frames"] == start
        check("settled_controls_do_not_keep_redrawing")
        old = first.dial
        first.controls[0] = GlassControl("gain", "example.dial", "Replacement", value=.1)
        workspace.update()
        wait(lambda: workspace.metrics()["applied"] > serial)
        workspace._native.present(first.id, old.id, old._generation, 0)
        pump(.1)
        assert first.controls[0].value == .1
        check("same_id_replacement_ignores_retired_generation")
        report.update(result="passed", metrics=workspace.metrics())
    except Exception as error:
        report.update(result="failed", error=repr(error))
        raise
    finally:
        workspace.close()
        owner.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
