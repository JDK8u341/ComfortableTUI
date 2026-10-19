"""Own-window checks for single-card close, workspace hide and workspace close."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time

from comfortable_tui import (BrowserService, FrameBuffer, GlassCanvas, GlassCard, GlassCardGroup,
                             GlassControl, GlassTheme, GlassWebView, GlassWorkspace, GpuFrameSource, OverlayCanvas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path(".build/glass-close.json"))
    parser.add_argument("--gpu-provider", type=Path)
    parser.add_argument("--with-browser", action="store_true")
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--material", choices=("liquid", "solid", "frosted"), default="liquid")
    parser.add_argument("--backdrop", choices=("none", "frosted"), default="frosted")
    parser.add_argument("--interactive", action="store_true", help="Leave own test windows open for manual clicks")
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    from comfortable_tui._win32_host import Win32
    qt = QApplication.instance() or QApplication([])
    api = Win32()
    api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    api.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.user.SendMessageW.restype = ctypes.c_ssize_t
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / Close regression host")
    owner.resize(430, 220)
    QVBoxLayout(owner).addWidget(QLabel(
        "Own test host\n\nCard X: close that card\nClose workspace: destroy all cards\n"
        "The host remains open until the test finishes.\nNo terminal input is automated."))
    owner.show()
    workspace = None
    report, events, canvas_events = dict(result="running", checks=[]), [], []
    monitored = set()
    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    def check(name):
        report["checks"].append(name)
        save()
        print("PASS " + name, flush=True)
    try:
        with ExitStack() as stack:
            provider = None
            if args.gpu_provider:
                provider = subprocess.Popen([str(args.gpu_provider.resolve())], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)
                def stop_provider():
                    provider.stdin.close()
                    try:
                        provider.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        provider.terminate()
                        provider.wait(timeout=3)
                    provider.stdout.close()
                stack.callback(stop_provider)
                line = provider.stdout.readline().split()
                if len(line) != 6 or line[0] != "CTUI_GPU_V1":
                    raise RuntimeError("GPU provider handshake failed")
                source = stack.enter_context(GpuFrameSource.attach(line[1]))
            else:
                source = stack.enter_context(FrameBuffer(160, 80))
                source.submit(bytes((80, 190, 160, 255)) * 160 * 80)
            canvas = OverlayCanvas(source, on_event=canvas_events.append)
            stack.callback(canvas.close)
            image = GlassCard("image", "Close this image card", [GlassCanvas("canvas", canvas, height=96)], width=280)
            keep = GlassCard("keep", "Workspace controls", [
                GlassControl("close_workspace", "button", "Close entire workspace")], width=280)
            page = None
            if args.with_browser:
                service = stack.enter_context(BrowserService(executable=args.browser))
                page = service.open("about:blank", width=232, height=96, render_mode=args.render_mode)
                stack.callback(page.close)
                web = GlassWebView("browser", page, height=96, auto_resize=False)
                stack.callback(web.canvas.close)
                extra = GlassCard("extra", "Independent browser", [web], width=280)
            else:
                extra = GlassCard("extra", "Another independent card", [GlassControl("info", "text", text="Stays open")], width=280)
            group = GlassCardGroup("cards", [keep, image, extra], layout="row")
            def on_event(event):
                events.append(event)
                if event.kind == "click" and event.control == "close_workspace":
                    workspace.close()
            workspace = stack.enter_context(GlassWorkspace([group], theme=GlassTheme(args.material, reduced_motion=True),
                                            backdrop=args.backdrop, on_event=on_event))
            workspace.start(int(owner.winId()))
            def windows():
                return {w["id"]: w for w in workspace.metrics().get("windows", [])}
            def pump(seconds):
                end = time.perf_counter() + seconds
                while time.perf_counter() < end:
                    qt.processEvents()
                    workspace.poll()
                    for hwnd in monitored:
                        assert api.user.IsWindow(hwnd) and api.user.IsWindowVisible(hwnd), "A sibling disappeared during single-card close"
                    time.sleep(.004)
            def wait(callback, timeout=12):
                end = time.perf_counter() + timeout
                while time.perf_counter() < end:
                    pump(.02)
                    value = callback()
                    if value:
                        return value
                raise TimeoutError(str(workspace.metrics()))
            def mouse(hwnd, message, x, y, flags=0):
                dpi = api.user.GetDpiForWindow(hwnd) / 96
                point = (round(y * dpi) & 0xffff) << 16 | (round(x * dpi) & 0xffff)
                api.user.SendMessageW(hwnd, message, flags, point)
            def click_close(card, *, release_inside=True):
                window = windows()[card]
                hwnd = window["hwnd"]
                width = window["rect"][2] / (api.user.GetDpiForWindow(hwnd) / 96)
                mouse(hwnd, 0x201, width - 26, 24, 1)
                mouse(hwnd, 0x202, width - 26 if release_inside else 50, 24)
                pump(.08)
            def painted(browser_page):
                stats = browser_page.frame_stats()
                if args.render_mode == "gpu":
                    assert stats["frame_backend"] == "native_gpu_d3d11" and stats["cpu_paints"] == 0
                    return stats.get("published_frames", 0) > 0
                assert stats["frame_backend"] == "native_cpu"
                return stats["cpu_paints"] > 0
            wait(lambda: len(windows()) == 3 and all(w["visible"] for w in windows().values()))
            wait(lambda: workspace.metrics().get("canvas_frames", 0) >= 1)
            if page:
                wait(lambda: page.loaded and bool(page._attachments))
                wait(lambda: painted(page))
            check("three_real_card_windows_and_image_consumer_present")
            if args.interactive:
                print("Click individual X buttons; use the native Close entire workspace button or close the host to finish.", flush=True)
                while not workspace.metrics().get("closed") and owner.isVisible():
                    pump(.02)
                workspace.close()
                check("manual_workspace_close_completed")
            else:
                image_hwnd = windows()["image"]["hwnd"]
                survivors = {name: windows()[name]["hwnd"] for name in ("keep", "extra")}
                monitored.update(survivors.values())
                click_close("image", release_inside=False)
                assert len(windows()) == 3 and not any(e.kind == "card_close_request" for e in events)
                check("releasing_close_button_outside_cancels_request")
                image.on_event = lambda e: e.kind == "card_close_request"
                click_close("image")
                assert len(workspace.cards) == 3 and api.user.IsWindow(image_hwnd)
                check("card_handler_can_cancel_its_close_request")
                image.on_event = None
                group.on_preview_event = lambda e: e.kind == "card_close_request"
                click_close("image")
                assert len(workspace.cards) == 3 and api.user.IsWindow(image_hwnd)
                check("group_preview_can_cancel_single_card_close")
                group.on_preview_event = None
                click_close("image")
                wait(lambda: "image" not in windows() and canvas._surface_host is None)
                assert not api.user.IsWindow(image_hwnd)
                assert {name: w["hwnd"] for name, w in windows().items()} == survivors
                assert workspace.visible and not workspace.metrics()["closed"]
                assert not any(e.kind in ("dismissed", "close_request") for e in events)
                assert sum(e.kind == "card_close_request" and e.card == "image" for e in events) == 1
                check("x_closes_only_one_card_preserving_siblings_and_workspace")
                assert sum(e.kind == "detached" for e in canvas_events) == 1
                source._check_open()
                if provider:
                    assert provider.poll() is None
                check("single_card_detaches_once_without_closing_caller_owned_provider")
                monitored.remove(survivors["extra"])
                api.user.SendMessageW(survivors["extra"], 0x10, 0, 0)  # WM_CLOSE on this fixture's own card.
                wait(lambda: "extra" not in windows())
                assert not api.user.IsWindow(survivors["extra"]) and len(windows()) == 1
                check("wm_close_also_targets_only_the_requested_card")
                if page:
                    wait(lambda: not page._attachments and not page._widgets)
                    assert not page._closed and not page.source._closed
                    report.setdefault("browser_frame_stats", []).append(page.frame_stats())
                    page.close()
                    assert page.source._closed
                    check("browser_detaches_before_caller_closes_page_and_releases_frames")
                monitored.clear()
                keep_hwnd = survivors["keep"]
                workspace.command("dismiss")
                wait(lambda: not api.user.IsWindowVisible(keep_hwnd))
                assert api.user.IsWindow(keep_hwnd) and not workspace.metrics()["closed"]
                workspace.show()
                wait(lambda: api.user.IsWindowVisible(keep_hwnd))
                assert windows()["keep"]["hwnd"] == keep_hwnd
                check("workspace_dismiss_hides_and_recall_preserves_the_window")
                click_close("keep")
                wait(lambda: not windows() and not workspace.metrics()["capture_active"]
                     and not workspace.metrics()["backdrop_visible"])
                assert not workspace.metrics()["closed"] and not workspace.cards
                check("closing_last_card_stops_backdrop_and_capture_but_keeps_workspace_reusable")
                group.children.extend([keep, image])
                workspace.set_cards(workspace.children)
                wait(lambda: "image" in windows() and canvas._surface_host is not None and not workspace._pending)
                assert sum(e.kind == "detached" for e in canvas_events) == 1
                check("closed_card_can_be_recreated_using_the_still_open_canvas")
                live_page = None
                if page:
                    live_page = service.open("about:blank", width=232, height=96, render_mode=args.render_mode)
                    stack.callback(live_page.close)
                    live_web = GlassWebView("browser", live_page, height=96, auto_resize=False)
                    stack.callback(live_web.canvas.close)
                    group.children.append(GlassCard("live-web", "Close with workspace", [live_web], width=280))
                    workspace.set_cards(workspace.children)
                    wait(lambda: "live-web" in windows() and live_page.loaded
                         and bool(live_page._attachments) and not workspace._pending)
                    wait(lambda: painted(live_page))
                keep_hwnd = windows()["keep"]["hwnd"]
                old_windows = [w["hwnd"] for w in windows().values()]
                mouse(keep_hwnd, 0x201, 70, 120, 1)
                mouse(keep_hwnd, 0x202, 70, 120)
                wait(lambda: workspace.metrics().get("closed"))
                assert all(not api.user.IsWindow(hwnd) for hwnd in old_windows)
                assert not workspace.metrics()["capture_active"] and not workspace._bound_controls
                assert sum(e.kind == "detached" for e in canvas_events) == 2
                assert api.user.IsWindow(int(owner.winId()))
                source._check_open()
                check("workspace_close_destroys_all_cards_and_consumers_preserving_host_and_sources")
                if live_page:
                    assert not live_page._widgets and not live_page._attachments
                    assert not live_page._closed and not live_page.source._closed
                    report.setdefault("browser_frame_stats", []).append(live_page.frame_stats())
                    live_page.close()
                    assert live_page.source._closed
                    check("workspace_close_detaches_active_browser_before_caller_releases_page")
                workspace.close()
                assert sum(e.kind == "detached" for e in canvas_events) == 2
                check("repeated_workspace_close_is_idempotent")
            report.update(result="passed", mode="gpu" if args.gpu_provider else "cpu", browser=args.with_browser,
                          browser_render_mode=args.render_mode if args.with_browser else None,
                          card_close_requests=sum(e.kind == "card_close_request" for e in events),
                          image_detach_count=sum(e.kind == "detached" for e in canvas_events),
                          workspace_closed=workspace.metrics()["closed"], capture_active=workspace.metrics()["capture_active"])
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
