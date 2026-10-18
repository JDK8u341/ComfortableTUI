"""Regression for focus-only stacking; operates on its own native windows."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time

from comfortable_tui import GlassCard, GlassCardGroup, GlassControl, GlassTheme, GlassWorkspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QWidget
    from comfortable_tui._win32_host import Win32
    app = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    api.user.GetTopWindow.argtypes = [wintypes.HWND]
    api.user.GetTopWindow.restype = wintypes.HWND
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / Focus regression host")
    owner.resize(400, 240)
    owner.showMaximized()
    cards = [GlassCard(name, name, [GlassControl("status", "text", text=name)], width=220,
                       draggable=name != "pulse") for name in ("session", "signal", "pulse", "appearance")]
    workspace = None
    report = dict(result="running", checks=[])
    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        workspace = GlassWorkspace(cards, theme=GlassTheme("solid", reduced_motion=True)).start(int(owner.winId()))

        def pump(seconds):
            end = time.perf_counter() + seconds
            while time.perf_counter() < end:
                app.processEvents()
                workspace.poll()
                time.sleep(.004)

        def wait(callback):
            end = time.perf_counter() + 8
            while time.perf_counter() < end:
                pump(.02)
                if callback():
                    return
            raise TimeoutError(str(workspace.metrics()))

        def windows():
            return {w["id"]: w["hwnd"] for w in workspace.metrics().get("windows", [])}

        def positions():
            result = {}
            for name, hwnd in windows().items():
                rect = wintypes.RECT()
                assert api.user.GetWindowRect(hwnd, ctypes.byref(rect))
                result[name] = (rect.left, rect.top, rect.right, rect.bottom)
            return result

        def order():
            names = {hwnd: name for name, hwnd in windows().items()}
            current, result = api.user.GetTopWindow(None), []
            for _ in range(4096):
                if not current:
                    return result
                if current in names:
                    result.append(names[current])
                current = api.user.GetWindow(current, 2)  # GW_HWNDNEXT
            raise AssertionError("window order traversal did not terminate")

        def click(name):
            hwnd = windows()[name]
            dpi = api.user.GetDpiForWindow(hwnd) / 96
            point = (round(55 * dpi) & 0xffff) << 16 | (round(50 * dpi) & 0xffff)
            api.user.SendMessageW(hwnd, 0x201, 1, point)
            api.user.SendMessageW(hwnd, 0x202, 0, point)
            wait(lambda: workspace.selected == name and order()[0] == name)
            pump(.08)

        def check(name):
            report["checks"].append(name)
            save()
            print("PASS " + name, flush=True)

        def stable_clicks(sequence):
            for name in sequence:
                before, before_order = positions(), order()
                click(name)
                assert positions() == before, (name, before, positions())
                assert [v for v in order() if v != name] == [v for v in before_order if v != name], (before_order, order())

        wait(lambda: len(windows()) == 4)
        pump(.2)
        assert workspace.metrics()["compact"]
        stable_clicks(("signal", "appearance", "session", "signal", "pulse"))
        check("automatic_compact_deck_focus_preserves_positions_and_other_siblings_order")
        workspace.configure(layout="deck")
        pump(.2)
        stable_clicks(("session", "appearance", "signal"))
        check("explicit_deck_focus_only_raises_selected_card")
        tools = GlassCardGroup("tools", cards[2:], layout="row")
        workspace.set_cards([cards[0], cards[1], tools])
        pump(.2)
        for name in ("appearance", "pulse", "session", "signal"):
            before = positions()
            click(name)
            assert positions() == before, (name, before, positions())
            if name in ("pulse", "appearance"):
                assert set(order()[:2]) == {"pulse", "appearance"}
        check("group_focus_raises_container_without_repositioning_its_children")
        workspace.set_cards(cards)
        workspace.configure(layout="focus")
        pump(.3)
        before = positions()
        previous = workspace.selected
        click("appearance")
        after = positions()
        assert before["appearance"] != after["appearance"]
        assert all(before[name] == after[name] for name in before if name not in (previous, "appearance"))
        check("explicit_focus_transition_only_moves_previous_and_new_focus")
        workspace.configure(layout="ribbon")
        pump(.2)
        before = positions()
        click("session")
        assert before != positions()
        check("explicit_ribbon_carousel_retains_selection_animation")
        owner.showNormal()
        owner.resize(400, 240)
        workspace.configure(layout="orbit")
        pump(.4)
        stable_clicks(("signal", "appearance", "pulse"))
        check("restored_owner_focus_preserves_orbit_or_compact_positions")
        workspace.configure(theme=GlassTheme("solid", reduced_motion=False))
        pump(1.4)
        for name in ("session", "signal", "appearance"):
            before = positions()
            click(name)
            for _ in range(12):
                pump(.025)
                assert positions() == before
        check("enabled_springs_do_not_animate_unrelated_cards_on_focus")
        workspace.close()
        assert workspace.metrics()["closed"] and not workspace.metrics()["windows"]
        check("native_windows_close_after_focus_regression")
        report["result"] = "passed"
    except Exception as error:
        report.update(result="failed", error=repr(error))
        raise
    finally:
        if workspace:
            workspace.close()
        owner.close()
        save()


if __name__ == "__main__":
    main()
