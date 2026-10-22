"""Own-window keyboard and real CEF card integration; no host JavaScript eval."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time
import uuid

from comfortable_tui import BrowserService, GlassCard, GlassCardGroup, GlassControl, GlassTheme, GlassWorkspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--focus-timeout", type=float, default=90)
    parser.add_argument("--hold", type=float, default=0)
    parser.add_argument("--inspectable-windows", action="store_true")
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    from glass_workspace.webview import local_site, make_cards
    from comfortable_tui._win32_host import Win32
    app = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    api.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.user.GetWindowThreadProcessId.restype = wintypes.DWORD
    class ThreadInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("active", wintypes.HWND), ("focus", wintypes.HWND), ("capture", wintypes.HWND),
                    ("menu", wintypes.HWND), ("move", wintypes.HWND), ("caret", wintypes.HWND), ("rect", wintypes.RECT)]
    api.user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(ThreadInfo)]
    api.user.GetGUIThreadInfo.restype = wintypes.BOOL
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / WebView keyboard verification")
    owner.resize(360, 220)
    QVBoxLayout(owner).addWidget(QLabel("Own-window browser/keyboard fixture\nActivate this host if requested.\nNo JavaScript eval permission."))
    owner.show()
    report, telemetry, events = dict(result="running", checks=[]), [], []
    workspace = None
    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with local_site() as origin, BrowserService(executable=args.browser) as service:
        def browser_event(event):
            if event["event"] == "message" and event["data"].get("topic") == "glass.telemetry":
                telemetry.append((event["view"], event["data"]["payload"]))
        pages, views, children = make_cards(service, origin, args.render_mode, browser_event)
        children[0].controls.insert(1, GlassControl("disabled", "button", "Disabled", enabled=False))
        children += [GlassCardGroup("hidden", [GlassCard("hidden-card", "Hidden")], visible=False),
                     GlassCardGroup("disabled", [GlassCard("disabled-card", "Disabled")], enabled=False)]
        try:
            workspace = GlassWorkspace(children, theme=GlassTheme("solid", reduced_motion=True), on_event=events.append)
            workspace.start(int(owner.winId()), inspectable_windows=args.inspectable_windows)
            def pump(seconds):
                end = time.perf_counter() + seconds
                while time.perf_counter() < end:
                    app.processEvents()
                    workspace.poll()
                    time.sleep(.004)
            def wait(callback, timeout=12):
                end = time.perf_counter() + timeout
                while time.perf_counter() < end:
                    pump(.02)
                    value = callback()
                    if value:
                        return value
                raise TimeoutError(str(workspace.metrics()))
            def windows():
                return {w["id"]: w for w in workspace.metrics().get("windows", [])}
            wait(lambda: len(windows()) == 5)
            native_thread = api.user.GetWindowThreadProcessId(windows()["navigation"]["hwnd"], None)
            def actual_focus():
                info = ThreadInfo()
                info.cbSize = ctypes.sizeof(info)
                assert api.user.GetGUIThreadInfo(native_thread, ctypes.byref(info))
                return info.focus
            def check(name):
                report["checks"].append(name)
                save()
                print("PASS " + name, flush=True)
            def focused(card, control=""):
                return (actual_focus() == windows()[card]["hwnd"] and
                        windows()[card]["focused_control"] == control)
            def key(code):
                hwnd = actual_focus()
                assert hwnd in {w["hwnd"] for w in windows().values()}
                api.user.SendMessageW(hwnd, 0x100, code, 1)
                api.user.SendMessageW(actual_focus(), 0x101, code, (1 << 31) | 1)
                pump(.08)
            def command(action, card, control=""):
                workspace.command(action)
                wait(lambda: focused(card, control))
            workspace.command("focus_card")
            print("FOCUS WebView keyboard verification owner if foreground permission is denied", flush=True)
            wait(lambda: workspace.metrics()["active"], timeout=args.focus_timeout)
            wait(lambda: all(page.loaded for page in pages) and len(telemetry) >= 2)
            wait(lambda: workspace.metrics()["canvas_frames"] >= 2)
            check("two_independent_browser_pages_present_inside_native_cards")
            wait(lambda: focused("navigation"))
            key(9)
            wait(lambda: focused("navigation", "first"))
            key(9)
            wait(lambda: focused("navigation", "second"))
            key(9)
            wait(lambda: focused("notes"))
            key(9)
            wait(lambda: focused("notes", "browser"))
            assert not views[0].interacting
            check("tab_skips_disabled_controls_and_crosses_card_headers_and_controls")
            key(9)
            wait(lambda: focused("inbox"))
            key(9)
            wait(lambda: focused("inbox", "browser"))
            key(9)
            wait(lambda: focused("navigation"))
            check("tab_wrap_skips_hidden_and_disabled_cards")
            command("previous_control", "inbox", "browser")
            command("previous_control", "inbox")
            command("previous_control", "notes", "browser")
            check("backward_tab_path_crosses_to_previous_cards_last_control")
            key(39)  # Non-interacting browser arrows return to card navigation.
            wait(lambda: focused("inbox"))
            key(39)
            wait(lambda: focused("navigation"))
            key(38)
            wait(lambda: focused("inbox"))
            check("header_arrows_transfer_real_keyboard_focus_between_eligible_cards")
            command("previous", "notes")
            key(9)
            wait(lambda: focused("notes", "browser"))
            key(13)
            wait(lambda: views[0].interacting)
            key(27)
            wait(lambda: not views[0].interacting)
            key(39)
            wait(lambda: focused("inbox"))
            check("enter_and_escape_control_page_interaction_without_trapping_card_navigation")
            def inspect(page):
                nonce = uuid.uuid4().hex
                service.publish("glass.commands", dict(op="inspect", nonce=nonce),
                                message_group="glass-demo", target=page.id)
                return wait(lambda: next((data for view, data in reversed(telemetry) if view == page.id and data.get("nonce") == nonce), None))
            def click(card, box):
                w = windows()[card]
                dpi = api.user.GetDpiForWindow(w["hwnd"]) / 96
                # The first canvas starts after the 102-DIP title and 22-DIP label.
                x = round((24 + box["x"] + box["width"] / 2) * dpi)
                y = round((124 + box["y"] + box["height"] / 2) * dpi)
                api.user.SendMessageW(w["hwnd"], 0x201, 1, (y & 0xffff) << 16 | (x & 0xffff))
                api.user.SendMessageW(w["hwnd"], 0x202, 0, (y & 0xffff) << 16 | (x & 0xffff))
                pump(.15)
            state = inspect(pages[0])
            click("notes", state["entry"])
            wait(lambda: views[0].interacting)
            for char in "Card 中文":
                api.user.SendMessageW(windows()["notes"]["hwnd"], 0x102, ord(char), 1)
            wait(lambda: inspect(pages[0])["value"] == "Card 中文")
            check("native_mouse_and_committed_unicode_text_reach_browser_input")
            key(8)
            wait(lambda: inspect(pages[0])["value"] == "Card 中")
            api.user.SendMessageW(windows()["notes"]["hwnd"], 0x102, ord("文"), 1)
            key(13)
            wait(lambda: inspect(pages[0])["clicks"] >= 1)
            check("browser_backspace_and_enter_keep_native_editing_and_form_submission")
            click("notes", state["send"])
            wait(lambda: (inspect(pages[1]).get("received") or {}).get("text") == "Card 中文")
            assert inspect(pages[1])["value"] == ""
            check("scoped_browser_messages_link_distinct_documents_without_mirroring_inputs")
            old = pages[0].source
            pages[0].resize(360, 280)
            intermediate = pages[0].source
            pages[0].resize(352, 300)
            wait(lambda: views[0]._acknowledged_source == pages[0].source.info)
            assert old._closed and intermediate._closed and not pages[0].source._closed
            check("rapid_browser_resizes_keep_retired_sources_until_native_ack")
            group = children[1]
            group.visible = False
            workspace.update()
            wait(lambda: not views[0]._view_visible() and not views[1]._view_visible())
            assert not views[0].interacting and not views[1].interacting
            group.visible = True
            workspace.update()
            wait(lambda: views[0]._view_visible() and views[1]._view_visible())
            check("group_visibility_pauses_pages_and_releases_browser_focus")
            report["frame_stats"] = [page.frame_stats() for page in pages]
            if args.render_mode == "gpu":
                assert all(s["frame_backend"] == "native_gpu_d3d11" and s["cpu_paints"] == 0 for s in report["frame_stats"])
            check("selected_browser_transport_matches_native_frame_backend")
            pump(args.hold)
            displayed = pages[1].source
            pages[1].close()
            wait(lambda: not views[1].enabled and not views[1].interacting)
            displayed._check_open()
            workspace.close()
            assert displayed._closed and not pages[0]._attachments and not pages[1]._attachments
            check("closing_a_displayed_page_disables_input_and_defers_buffer_release_until_detach")
            report["result"] = "passed"
            report["render_mode"] = args.render_mode
            save()
        except Exception as error:
            report.update(result="failed", error=repr(error))
            raise
        finally:
            if workspace:
                workspace.close()
            assert all(not page._attachments and not page._widgets for page in pages)
            for page in pages:
                page.close()
            owner.close()
            save()


if __name__ == "__main__":
    main()
