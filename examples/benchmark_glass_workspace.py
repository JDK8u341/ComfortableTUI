"""Automated multi-card stress in an owned window; no mouse/keyboard automation.

This fixture exercises the real compositor, not external-terminal attachment.
GPU producer processes keep their data path in C++; Python only updates state.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
import threading
import time

from comfortable_tui import (FrameBuffer, GlassBackground, GlassCanvas, GlassCard,
                             GlassEffect, GlassTheme, GlassWorkspace, GpuFrameSource)
from comfortable_tui import _native_glass


def gpu_source(stack, executable):
    process = subprocess.Popen([str(executable.resolve()), "--width", "128", "--height", "96",
                                "--fps", "120"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def stop():
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()

    stack.callback(stop)
    received = Queue()
    threading.Thread(target=lambda: received.put(process.stdout.readline()), daemon=True).start()
    line = received.get(timeout=10).split()
    if len(line) != 6 or line[0] != "CTUI_GPU_V1":
        raise RuntimeError("GPU producer did not supply a valid handshake")
    return stack.enter_context(GpuFrameSource.attach(line[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--producer", type=Path, default=Path(".build/glass-native/Release/ctui_gpu_producer.exe"))
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 4, 12])
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--churn", type=int, default=12)
    parser.add_argument("--report", type=Path, default=Path(".build/glass-workspace-benchmark.json"))
    args = parser.parse_args()
    if any(not 1 <= count <= 12 for count in args.counts) or not 1 <= args.seconds <= 120 or not 1 <= args.churn <= 100:
        parser.error("counts must be 1..12, seconds 1..120, churn 1..100")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget
    qt = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.setWindowTitle("Comfortable Glass / automated workspace stress")
    owner.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    owner.resize(560, 360)
    owner.show()
    qt.processEvents()
    results = []
    workspace = None
    pixels = bytes([40, 120, 220, 255]) * (128*96)

    def step():
        qt.processEvents()
        workspace.poll()

    def wait(predicate, timeout=15):
        deadline = time.perf_counter()+timeout
        while time.perf_counter() < deadline:
            step()
            if predicate():
                return
            time.sleep(.005)
        raise AssertionError("native workspace stress timed out: "+json.dumps(workspace.metrics()))

    def pump(seconds):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            step()
            time.sleep(.005)

    try:
        for count in args.counts:
            with ExitStack() as stack:
                sources = [gpu_source(stack, args.producer) if args.source == "gpu"
                           else stack.enter_context(FrameBuffer(128, 96)) for _ in range(count)]
                if args.source == "cpu":
                    for source in sources:
                        source.submit(pixels)
                canvas_cleanup = stack.enter_context(ExitStack())
                views = [GlassCanvas("video", source, height=96) for source in sources]
                for view in views:
                    canvas_cleanup.callback(view.canvas.close)
                cards = [GlassCard(f"card-{i}", f"Source {i+1}", [view], width=220,
                                   background=GlassBackground("solid", opacity=1, tint=0x172030))
                         for i, view in enumerate(views)]
                workspace = GlassWorkspace(cards, theme=GlassTheme("solid", reduced_motion=True))
                stack.callback(workspace.close)
                workspace.start(int(owner.winId()))
                wait(lambda: workspace.metrics().get("canvas_frames", 0) >= count and
                     len(workspace.metrics()["windows"]) == count)
                assert all(window["visible"] for window in workspace.metrics()["windows"])
                assert _native_glass.effect_diagnostics()["surface_bytes"] == 0
                baseline_start = workspace.metrics()
                pump(1)
                baseline_end = workspace.metrics()

                for view in views:
                    view.effects = [GlassEffect("tint", parameters=(.3,)),
                                    GlassEffect("blur", parameters=(4,)),
                                    GlassEffect("glow", parameters=(.4, 8))]
                workspace.update()
                wait(lambda: _native_glass.effect_diagnostics()["surface_bytes"] > 0)
                pump(.7)
                before = _native_glass.effect_diagnostics()
                start_metrics = workspace.metrics()
                serial = start_metrics["applied"]
                start_cpu, start = time.process_time(), time.perf_counter()
                updates = 0
                while time.perf_counter()-start < args.seconds:
                    amount = (updates % 60)/60
                    for card, view in zip(cards, views):
                        workspace.set_effect_parameters(card, view, 0, (amount,))
                    if args.source == "cpu":
                        for source in sources:
                            source.submit(pixels)
                    updates += 1
                    step()
                    time.sleep(1/120)
                wall = time.perf_counter()-start
                cpu = time.process_time()-start_cpu
                pump(.6)  # Native aggregate metrics are sampled every 500 ms.
                active = workspace.metrics()
                after = _native_glass.effect_diagnostics()
                assert active["applied"] == serial, "parameter updates submitted full scenes"
                assert before["surface_allocations"] == after["surface_allocations"]
                assert before["shader_compilations"] == after["shader_compilations"]
                assert active["effects_rendered"] > start_metrics["effects_rendered"]

                if args.source == "cpu":
                    pump(.7)
                    idle_passes = workspace.metrics()["effects_rendered"]
                    pump(.7)
                    idle_passes = workspace.metrics()["effects_rendered"]-idle_passes
                    assert idle_passes == 0, "static inputs continued executing effect passes"
                    gil_frames = None
                else:
                    idle_passes = None  # The native producer intentionally continues publishing.
                    previous_interval = sys.getswitchinterval()
                    try:
                        sys.setswitchinterval(1)
                        deadline = time.perf_counter()+.7
                        while time.perf_counter() < deadline:
                            pass
                        # Discard the first snapshot: native aggregate reports can
                        # lag by 500 ms. Both snapshots below follow a held-GIL
                        # interval with no Python/Qt event-loop pumping.
                        previous_frames = workspace.metrics()["canvas_frames"]
                        deadline = time.perf_counter()+.7
                        while time.perf_counter() < deadline:
                            pass  # Hold the GIL; do not pump Qt or workspace events.
                    finally:
                        sys.setswitchinterval(previous_interval)
                    gil_frames = workspace.metrics()["canvas_frames"]-previous_frames
                    assert gil_frames > 0, "native GPU transport stopped while Python held the GIL"

                # Repeated geometry changes, same-ID replacement, removal/re-addition.
                max_owned = after["surface_bytes"]
                for cycle in range(args.churn):
                    owner.move(owner.x()+(-1 if cycle % 2 else 1)*8, owner.y())
                    owner.resize(560+(cycle % 3)*80, 360+(cycle % 2)*70)
                    old = views[0]
                    replacement = GlassCanvas("video", sources[0], height=96+(cycle % 3)*8,
                                               effects=old.effects)
                    canvas_cleanup.callback(replacement.canvas.close)
                    cards[0].width = 220+(cycle % 3)*16
                    cards[0].controls = [replacement]
                    views[0] = replacement
                    workspace.update()
                    wait(lambda: old._workspace is None and replacement._workspace is workspace)
                    old.canvas.close()
                    workspace.remove_card(cards[-1].id)
                    wait(lambda: len(workspace.metrics()["windows"]) == count-1)
                    workspace.set_cards(cards)
                    wait(lambda: len(workspace.metrics()["windows"]) == count and views[-1]._workspace is workspace)
                    max_owned = max(max_owned, _native_glass.effect_diagnostics()["surface_bytes"])
                    assert max_owned <= 256*1024*1024

                # Release the display before the application-owned sources/providers.
                close_start = time.perf_counter()
                workspace.close()
                close_ms = (time.perf_counter()-close_start)*1000
                assert _native_glass.effect_diagnostics()["surface_bytes"] == 0
                assert all(view._workspace is None for view in views)
                assert all(not source._closed for source in sources)
                results.append(dict(cards=count, source=args.source, parameter_batches=updates,
                                    measured_seconds=wall, parent_cpu_seconds=cpu,
                                    baseline_presents=baseline_end["presents"]-baseline_start["presents"],
                                    active_presents=active["presents"]-start_metrics["presents"],
                                    active_canvas_frames=active["canvas_frames"]-start_metrics["canvas_frames"],
                                    effect_passes=active["effects_rendered"]-start_metrics["effects_rendered"],
                                    steady_surface_allocations=0, steady_shader_compiles=0,
                                    idle_effect_passes=idle_passes, native_frames_during_gil_hold=gil_frames,
                                    max_owned_effect_bytes=max_owned, churn_cycles=args.churn,
                                    close_ms=close_ms, remaining_effect_bytes=0))
        report = dict(result="passed", fixture="application-owned QWidget; real D3D11 compositor",
                      physical_dpi=ctypes.windll.user32.GetDpiForWindow(int(owner.winId())),
                      cases=results, diagnostics=_native_glass.effect_diagnostics())
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(report))
    except Exception as error:
        report = dict(result="failed", completed_cases=results, error=str(error),
                      metrics=workspace.metrics() if workspace is not None else {})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        raise
    finally:
        if workspace is not None:
            workspace.close()
        owner.close()
        qt.processEvents()


if __name__ == "__main__":
    main()
