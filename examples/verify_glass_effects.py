"""Verify real compositor effects in an application-owned window; no simulated input."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import time

from comfortable_tui import (FrameBuffer, GlassBackground, GlassCanvas, GlassCard, GlassControl,
                             GlassEffect, GlassTheme, GlassWorkspace, register_glass_effects)


def load_effect_extension(folder):
    path = next(Path(folder).glob("ctui_glass_effects*.pyd"))
    spec = importlib.util.spec_from_file_location("ctui_glass_effects", path.resolve())
    extension = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extension)
    register_glass_effects(extension)
    return extension


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, default=Path(".build/glass-controls"))
    parser.add_argument("--report", type=Path, default=Path(".build/glass-effects.json"))
    parser.add_argument("--webview", action="store_true", help="Also run real CEF CPU/GPU frame integration")
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    qt = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / effects verification")
    owner.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    owner.resize(560, 360)
    owner.show()
    qt.processEvents()
    extension = load_effect_extension(args.extension)
    checks = []
    workspace = None

    def wait(predicate, timeout=10):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            qt.processEvents()
            workspace.poll()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("verification timed out")

    with FrameBuffer(64, 64) as source:
        view = GlassCanvas("canvas", source, height=96,
                           effects=[GlassEffect("example.negative", parameters=(.8,))])
        note = GlassControl("note", "text", text="Blur and glow",
                            effects=[GlassEffect("liquid", "background", (8, .35)),
                                     GlassEffect("tint", "background", (.1,), tint=0x85E5D1),
                                     GlassEffect("blur", parameters=(1,)),
                                     GlassEffect("glow", parameters=(.6, 6), tint=0x85E5D1)])
        card = GlassCard("effect", "Independent effects", [note, view],
                         background=GlassBackground("solid", tint=0x172030, opacity=1), width=280,
                         effects=[GlassEffect("tint", parameters=(.03,), tint=0x85E5D1)])
        sibling = GlassCard("plain", "Direct path", [GlassControl("text", "text", text="No effects")], width=240)
        # This test owns its QWidget; it does not test external terminal attachment.
        workspace = GlassWorkspace([card, sibling], theme=GlassTheme("solid", reduced_motion=True))
        try:
            source.submit(bytes([40, 120, 220, 255]) * (64 * 64))
            workspace.start(int(owner.winId()))
            wait(lambda: extension.stats()["draws"] > 0 and workspace.metrics().get("effects_rendered", 0) > 0)
            initial_metrics = workspace.metrics()
            checks.append("native_callback_and_builtin_passes_in_real_compositor")
            before = extension.stats()
            serial = workspace.metrics()["applied"]
            workspace.set_effect_parameters(card, view, 0, [.2])
            wait(lambda: extension.stats()["draws"] > before["draws"])
            assert workspace.metrics()["applied"] == serial
            assert extension.stats()["created"] == before["created"]
            checks.append("parameter_update_without_scene_submission_or_resource_recreation")
            wait(lambda: workspace.metrics().get("canvas_frames", 0) >= 1)
            idle = extension.stats()["draws"]
            end = time.perf_counter()+.4
            while time.perf_counter() < end:
                qt.processEvents()
                workspace.poll()
                time.sleep(.01)
            assert extension.stats()["draws"] == idle
            checks.append("unchanged_input_has_no_idle_effect_draws")
            for _ in range(4):
                source.submit(bytes([220, 40, 120, 255]) * (64 * 64))
                end = time.perf_counter()+.04
                while time.perf_counter() < end:
                    qt.processEvents()
                    workspace.poll()
                    time.sleep(.005)
            assert extension.stats()["draws"] > idle
            checks.append("new_frame_uses_existing_single_consumer")
            for _ in range(4):
                previous = view
                view = GlassCanvas("canvas", source, height=96,
                                   effects=[GlassEffect("example.negative", parameters=(.6,))])
                card.controls[-1] = view
                before = extension.stats()["draws"]
                workspace.update()
                wait(lambda: extension.stats()["draws"] > before and
                     extension.stats()["created"]-extension.stats()["released"] == 1 and
                     previous._workspace is None)
                previous.canvas.close()
            checks.append("same_id_replacement_keeps_one_live_instance_and_one_consumer")
            workspace.remove_card("effect")
            wait(lambda: extension.stats()["created"] == extension.stats()["released"])
            checks.append("detach_releases_effect_resources_before_source_close")
            if args.webview:
                from comfortable_tui import BrowserService, GlassWebView
                from examples.glass_workspace.webview import local_site
                with local_site() as origin, BrowserService(executable=args.browser) as service:
                    for mode in ("cpu", "gpu"):
                        page = service.open(origin+"/webview-card.html?name=Effects", origins=[origin],
                                            width=200, height=96, render_mode=mode)
                        browser = GlassWebView("browser", page, height=96, auto_resize=False,
                                               effects=[GlassEffect("example.negative", parameters=(.2,))])
                        web_card = GlassCard("web", "WebView effects / "+mode, [browser], width=280)
                        before = extension.stats()
                        workspace.set_cards([sibling, web_card])
                        wait(lambda: page.loaded and extension.stats()["draws"] > before["draws"])
                        assert page._attachments
                        workspace.remove_card("web")
                        wait(lambda: extension.stats()["released"] == extension.stats()["created"] and browser._workspace is None)
                        assert not page._attachments and not page._closed
                        browser.canvas.close()
                        page.close()
                        checks.append(mode+"_webview_effect_uses_existing_frame_and_detaches_cleanly")
            workspace.close()
            assert extension.stats()["wrong_thread"] == 0
            assert not source._closed
            result = dict(result="passed", checks=checks, extension=extension.stats(),
                          initial_metrics=initial_metrics, idle_draws_in_400ms=0, metrics=workspace.metrics())
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
            print(json.dumps(result))
        finally:
            workspace.close()
            view.canvas.close()
            owner.close()
            qt.processEvents()


if __name__ == "__main__":
    main()
