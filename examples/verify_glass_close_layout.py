"""Native position regression fixture; the interactive terminal example is glass_workspace.closing."""
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
    parser.add_argument("--report", type=Path, default=Path(".build/glass-close-layout.json"))
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QWidget
    from comfortable_tui._win32_host import Win32
    qt = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / Close positions regression fixture")
    owner.resize(430, 220)
    owner.move(350, 180)
    owner.show()
    report = dict(result="running", cases=[], reflows_moved=0)
    workspace = None

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def positions():
        result = {}
        for w in workspace.metrics().get("windows", []):
            r = wintypes.RECT()
            if not api.user.GetWindowRect(w["hwnd"], ctypes.byref(r)):
                # Destruction can precede the next published metrics snapshot.
                assert not api.user.IsWindow(w["hwnd"])
                continue
            result[w["id"]] = (w["hwnd"], (r.left, r.top, r.right, r.bottom))
        return result

    def pump(seconds, expected=None):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            qt.processEvents()
            workspace.poll()
            if expected:
                for name, (hwnd, rectangle) in expected.items():
                    r = wintypes.RECT()
                    assert api.user.IsWindowVisible(hwnd), (name, "hidden")
                    assert api.user.GetWindowRect(hwnd, ctypes.byref(r))
                    actual = (r.left, r.top, r.right, r.bottom)
                    assert actual == rectangle, (name, rectangle, actual)
            time.sleep(.004)

    def wait(predicate, expected=None):
        end = time.perf_counter() + 12
        while time.perf_counter() < end:
            pump(.02, expected)
            if predicate():
                return
        raise TimeoutError(str(workspace.metrics()))

    def settle(count):
        wait(lambda: len(positions()) == count and all(w["visible"] for w in workspace.metrics()["windows"]))
        previous, changed = positions(), time.perf_counter()
        def stable():
            nonlocal previous, changed
            current = positions()
            if current != previous:
                previous, changed = current, time.perf_counter()
            return time.perf_counter() - changed >= .4
        wait(stable)

    try:
        for maximized in (False, True):
            if maximized:
                owner.showMaximized()
            for layout in ("orbit", "deck", "focus", "ribbon"):
                for topology in ("roots", "row", "column", "nested"):
                    cards = [GlassCard(name, name, [GlassControl("status", "text", text=name)], width=240)
                             for name in ("a", "b", "c")]
                    roots = cards
                    if topology == "nested":
                        roots = [GlassCardGroup("outer", [cards[0], GlassCardGroup("inner", cards[1:], layout="column")])]
                    elif topology != "roots":
                        roots = [GlassCardGroup("group", cards, layout=topology)]
                    workspace = GlassWorkspace(roots, layout=layout, theme=GlassTheme("solid"))
                    workspace.selected = "b"
                    workspace.start(int(owner.winId()))
                    settle(3)
                    before = positions()
                    assert len(before) == 3 and not any(workspace.metrics()["windows"][i]["pinned"] for i in range(3))
                    expected = {name: value for name, value in before.items() if name != "b"}
                    api.user.SendMessageW(before["b"][0], 0x10, 0, 0)
                    wait(lambda: set(positions()) == {"a", "c"}, expected)
                    pump(.45, expected)
                    assert set(positions()) == {"a", "c"}
                    workspace.cards[-1].controls[0].text = "Updated without rearranging"
                    workspace.update()
                    pump(.12, expected)
                    # Removing the now-selected fallback must also keep its sibling stable.
                    api.user.SendMessageW(before["a"][0], 0x10, 0, 0)
                    wait(lambda: set(positions()) == {"c"}, {"c": expected["c"]})
                    pump(.35, {"c": expected["c"]})
                    assert set(positions()) == {"c"}
                    workspace.command("reflow")
                    pump(1.3)
                    if positions()["c"][1] != expected["c"][1]:
                        report["reflows_moved"] += 1
                    assert not workspace.metrics()["windows"][0]["pinned"], "Retained placement must not turn into a drag pin"
                    workspace.close()
                    workspace = None
                    report["cases"].append(dict(layout=layout, topology=topology, maximized=maximized,
                                                selected_close=True, repeated_close=True, value_update=True))
                    save()
                    print(f"PASS {layout}/{topology}/maximized={maximized}", flush=True)
        assert report["reflows_moved"] > 0, "Explicit reflow must release retained placement"
        owner.showNormal()
        owner.setGeometry(350, 180, 430, 220)
        cards = [GlassCard(name, name, [GlassControl("status", "text", text=name)], width=240)
                 for name in ("a", "b", "c")]
        workspace = GlassWorkspace(cards, layout="deck", theme=GlassTheme("solid")).start(int(owner.winId()))
        settle(3)
        before = positions()
        workspace.remove_card("b")
        wait(lambda: set(positions()) == {"a", "c"}, {name: value for name, value in before.items() if name != "b"})
        pump(.4, {name: value for name, value in before.items() if name != "b"})
        owner.resize(640, 360)
        pump(1.6)
        resized = positions()
        workspace.command("reflow")
        pump(1.2)
        assert positions() == resized, "Resize must release retained placement before explicit reflow"
        # Retain a gap again, then add a node: an addition is a fresh layout.
        workspace.remove_card("a")
        wait(lambda: set(positions()) == {"c"}, {"c": resized["c"]})
        pump(.4, {"c": resized["c"]})
        workspace.set_cards([*workspace.cards, cards[1]])
        settle(2)
        added = positions()
        assert set(added) == {"b", "c"}
        workspace.command("reflow")
        pump(1.2)
        assert positions() == added, "Adding a card must release retained placement"
        report["resize_and_addition_release_retained_placement"] = True
        workspace.close()
        workspace = None
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
