"""Single-card and workspace close checks attached to the running terminal."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time

from comfortable_tui import (App, Button, FrameBuffer, GlassCanvas, GlassCard, GlassCardGroup,
                             GlassControl, GlassTheme, GlassWorkspace, HBox, Label, Log, OverlayCanvas,
                             Panel, VBox)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Check this terminal's own card windows automatically")
    parser.add_argument("--grouped", action="store_true", help="Use nested row/column containers")
    parser.add_argument("--layout", choices=("orbit", "deck", "focus", "ribbon"), default="orbit")
    parser.add_argument("--material", choices=("liquid", "solid", "frosted"), default="liquid")
    parser.add_argument("--backdrop", choices=("none", "frosted"), default="frosted")
    parser.add_argument("--report", type=Path, default=Path(".build/glass-terminal-close.json"))
    args = parser.parse_args()
    from comfortable_tui._win32_host import Win32, _TERMINALS
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    report = dict(result="running", checks=[], host="real_terminal", grouped=args.grouped, layout=args.layout)
    log = Log()
    log.write("X closes one card. Its siblings should stay exactly where they were.")
    log.write("Close entire workspace closes all cards; this terminal TUI stays open.")
    log.write("Recreate cards starts a new workspace. F6 enters cards; Esc returns; Ctrl+Q quits.")
    workspace = None
    started = time.perf_counter()
    phase, deadline, fixed, old_windows = "ready", 0., {}, []

    def windows():
        return {w["id"]: w for w in workspace.metrics().get("windows", [])}

    def rect(hwnd):
        value = wintypes.RECT()
        assert api.user.GetWindowRect(hwnd, ctypes.byref(value))
        return value.left, value.top, value.right, value.bottom

    def click(hwnd, x, y):
        scale = api.user.GetDpiForWindow(hwnd) / 96
        point = (round(y * scale) & 0xffff) << 16 | (round(x * scale) & 0xffff)
        api.user.SendMessageW(hwnd, 0x201, 1, point)
        api.user.SendMessageW(hwnd, 0x202, 0, point)

    def passed(name):
        report["checks"].append(name)
        log.write("PASS " + name)

    try:
        with FrameBuffer(160, 80) as source:
            source.submit(bytes((80, 190, 160, 255)) * 160 * 80)
            canvas = OverlayCanvas(source)
            try:
                def close_workspace():
                    if workspace:
                        close_start = time.perf_counter()
                        workspace.close()
                        report["workspace_close_ms"] = (time.perf_counter() - close_start) * 1000
                        report["native_teardown"] = {k: v for k, v in workspace.metrics().items() if k.startswith("close_")}
                        log.write(f"Workspace closed in {report['workspace_close_ms']:.1f}ms. Terminal and image buffer remain open.")

                def on_event(event):
                    if event.kind == "card_close_request":
                        log.write("Close requested: " + event.card)
                    if event.kind == "click" and event.control == "close_workspace":
                        close_workspace()

                def recreate():
                    nonlocal workspace
                    if workspace:
                        workspace.close()
                    cards = [
                        GlassCard("keep", "Workspace controls", [GlassControl(
                            "close_workspace", "button", "Close entire workspace")], width=280),
                        GlassCard("image", "Close this image card", [GlassCanvas("canvas", canvas, height=96)], width=280),
                        GlassCard("extra", "Independent sibling", [GlassControl("status", "text", text="Stays in place")], width=280),
                    ]
                    roots = [GlassCardGroup("outer", [cards[0], GlassCardGroup("inner", cards[1:], layout="column")],
                                            layout="row")] if args.grouped else cards
                    workspace = GlassWorkspace(roots, layout=args.layout, theme=GlassTheme(args.material),
                                               backdrop=args.backdrop, on_event=on_event)
                    workspace.bind(app)
                    log.write("Workspace created around this terminal.")

                def tick():
                    nonlocal phase, deadline, fixed, old_windows
                    if not args.self_test:
                        return
                    now = time.perf_counter()
                    if now - started > 30:
                        raise TimeoutError(f"Terminal close test timed out in {phase}: {workspace.metrics()}")
                    for hwnd, before in fixed.items():
                        assert api.user.IsWindowVisible(hwnd), "A sibling disappeared"
                        assert rect(hwnd) == before, "A sibling moved during single-card close"
                    ws = windows()
                    if phase == "ready" and len(ws) == 3 and all(w["visible"] for w in ws.values()):
                        # Verify actual native ownership, not just a Python bind flag.
                        owner = api.user.GetWindow(ws["keep"]["hwnd"], 4)  # GW_OWNER
                        assert api.class_name(owner) in _TERMINALS
                        assert all(api.user.GetWindow(w["hwnd"], 4) == owner for w in ws.values())
                        report.update(terminal_class=api.class_name(owner), terminal_hwnd=int(owner))
                        passed("all_cards_owned_by_the_actual_terminal_window")
                        workspace.command("select", "image")
                        deadline, phase = now + 2, "settle"
                    elif phase == "settle" and now >= deadline and workspace.metrics().get("canvas_frames", 0):
                        fixed = {ws[name]["hwnd"]: rect(ws[name]["hwnd"]) for name in ("keep", "extra")}
                        report["survivor_rectangles_before"] = list(fixed.values())
                        image = ws["image"]
                        click(image["hwnd"], image["rect"][2] / (api.user.GetDpiForWindow(image["hwnd"]) / 96) - 26, 24)
                        deadline, phase = now + 1, "single"
                    elif phase == "single" and now >= deadline:
                        assert set(ws) == {"keep", "extra"} and canvas._surface_host is None
                        source._check_open()
                        passed("single_card_close_preserves_sibling_rectangles_visibility_and_source")
                        workspace.cards[-1].controls[0].text = "Live content update after closing"
                        workspace.update()
                        deadline, phase = now + .5, "update"
                    elif phase == "update" and now >= deadline:
                        passed("content_update_keeps_the_retained_positions")
                        report["survivor_rectangles_after"] = [rect(hwnd) for hwnd in fixed]
                        fixed.clear()
                        old_windows = [w["hwnd"] for w in ws.values()]
                        click(ws["keep"]["hwnd"], 70, 120)
                        phase = "workspace"
                    elif phase == "workspace" and workspace.metrics().get("closed"):
                        assert all(not api.user.IsWindow(hwnd) for hwnd in old_windows)
                        assert api.user.IsWindow(report["terminal_hwnd"])
                        assert not workspace.metrics()["capture_active"]
                        source._check_open()
                        passed("native_workspace_button_closes_all_cards_preserving_terminal_and_source")
                        report["result"] = "passed"
                        app.exit()

                root = VBox(Label("GLASS / CLOSE TEST · attached to your terminal", height=1),
                            Panel(log, "Close events and verification"),
                            HBox(Button("Recreate cards", recreate), Button("Close workspace", close_workspace),
                                 Button("Quit", lambda: app.exit()), height=1), gap=1)
                with App(root) as app:
                    recreate()
                    app.bind("f6", lambda: workspace.command("focus_card") if not workspace.metrics().get("closed") else None)
                    app.call_later(.02, tick, repeat=True)
                    app.run()
                    if not args.self_test:
                        report["result"] = "manual_session_completed"
            finally:
                if workspace:
                    workspace.close()
                canvas.close()
    except Exception as error:
        report.update(result="failed", error=repr(error))
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
