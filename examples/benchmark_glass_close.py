"""Measure synchronous workspace shutdown; default host is the actual terminal."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import threading
import time

from comfortable_tui import (App, GlassCard, GlassControl, GlassTheme, GlassWorkspace,
                             Label, Log, Panel, VBox)


class _CloseTimer:
    """Benchmark-only wrapper; retain the original object's normal lifetime."""
    def __init__(self, target, result, name):
        self.target, self.result, self.name = target, result, name

    def __getattr__(self, name):
        return getattr(self.target, name)

    def close(self):
        start = time.perf_counter()
        self.target.close()
        self.result[self.name] = (time.perf_counter() - start) * 1000


def measure_close(workspace, api):
    windows = [w["hwnd"] for w in workspace.metrics()["windows"]]
    assert windows and all(api.user.IsWindowVisible(hwnd) for hwnd in windows)
    result = {}
    workspace._native = _CloseTimer(workspace._native, result, "native_close_ms")
    if workspace._tracker:
        workspace._tracker = _CloseTimer(workspace._tracker, result, "tracker_close_ms")
    begin, ready, stop = threading.Event(), threading.Event(), threading.Event()
    start = 0.

    def observe():
        ready.set()
        begin.wait()
        while not stop.is_set():
            if "all_hidden_ms" not in result and not any(api.user.IsWindowVisible(hwnd) for hwnd in windows):
                result["all_hidden_ms"] = (time.perf_counter() - start) * 1000
            if not any(api.user.IsWindow(hwnd) for hwnd in windows):
                result["all_destroyed_ms"] = (time.perf_counter() - start) * 1000
                return
            stop.wait(.001)

    observer = threading.Thread(target=observe, name="glass-close-observer")
    observer.start()
    ready.wait()
    start = time.perf_counter()
    begin.set()
    try:
        workspace.close()
        elapsed = (time.perf_counter() - start) * 1000
    finally:
        stop.set()
        observer.join()
    assert all(not api.user.IsWindow(hwnd) for hwnd in windows)
    assert workspace.metrics()["closed"] and not workspace.metrics()["capture_active"]
    result.setdefault("all_hidden_ms", elapsed)
    result.setdefault("all_destroyed_ms", elapsed)
    result["close_ms"] = elapsed
    result["native_teardown"] = {k: v for k, v in workspace.metrics().items() if k.startswith("close_")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=int, nargs="+", default=[3, 8, 12])
    parser.add_argument("--materials", nargs="+", choices=("solid", "liquid"), default=["liquid"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--fixture", action="store_true", help="Explicit Qt benchmark owner, not a terminal")
    parser.add_argument("--report", type=Path, default=Path(".build/glass-close-benchmark.json"))
    args = parser.parse_args()
    if args.repeat < 1 or any(not 1 <= n <= 12 for n in args.cards):
        parser.error("repeat must be positive and card counts must be 1..12")
    from comfortable_tui._win32_host import Win32, _TERMINALS
    api = Win32()
    report = dict(result="running", host="qt_fixture" if args.fixture else "real_terminal", samples=[])
    cases = iter((material, count, trial) for material in args.materials for count in args.cards for trial in range(args.repeat))
    workspace, owner, app = None, None, None
    ready_at, created_at = 0., 0.
    current = None
    status, log = Label("Starting workspace shutdown benchmark", height=1), Log()

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def tick():
        nonlocal workspace, ready_at, created_at, current
        now = time.perf_counter()
        if workspace is None:
            current = next(cases, None)
            if current is None:
                report["summary"] = []
                for material in args.materials:
                    for count in args.cards:
                        samples = [s for s in report["samples"] if s["material"] == material and s["cards"] == count]
                        report["summary"].append(dict(material=material, cards=count, samples=len(samples),
                            **{key + "_median": statistics.median(s[key] for s in samples)
                               for key in ("close_ms", "all_hidden_ms", "all_destroyed_ms")}))
                report["result"] = "passed"
                if app:
                    app.exit()
                return
            material, count, trial = current
            cards = [GlassCard(str(i), f"Close benchmark / {i + 1}", [GlassControl("status", "text", text="Native card")], width=240)
                     for i in range(count)]
            workspace = GlassWorkspace(cards, theme=GlassTheme(material), backdrop="frosted" if material == "liquid" else "none")
            if app:
                workspace.bind(app)
            else:
                workspace.start(int(owner.winId()))
            status.set_text(f"{material} / {count} cards / sample {trial + 1}")
            ready_at, created_at = 0., now
            return
        metrics = workspace.metrics()
        windows = metrics.get("windows", [])
        count = current[1]
        if now - created_at > 20:
            raise TimeoutError(str(metrics))
        if len(windows) != count or not all(w["visible"] for w in windows) or metrics.get("presents", 0) < count:
            return
        if not ready_at:
            ready_at = now + .5
        if now < ready_at:
            return
        host = api.user.GetWindow(windows[0]["hwnd"], 4)
        if app:
            assert api.class_name(host) in _TERMINALS
        sample = dict(material=current[0], actual_material=metrics["material"], cards=count, trial=current[2],
                      **measure_close(workspace, api))
        report["samples"].append(sample)
        log.write(f"{count} cards: hidden {sample['all_hidden_ms']:.1f}ms / close {sample['close_ms']:.1f}ms")
        if args.fixture:
            print(json.dumps(sample), flush=True)
        workspace = None
        save()

    try:
        if args.fixture:
            from PySide6.QtWidgets import QApplication, QWidget
            qt = QApplication.instance() or QApplication([])
            owner = QWidget()
            owner.setWindowTitle("Comfortable Glass / Shutdown benchmark fixture (not a terminal)")
            owner.resize(430, 220)
            owner.show()
            while report["result"] == "running":
                qt.processEvents()
                if workspace:
                    workspace.poll()
                tick()
                time.sleep(.004)
        else:
            with App(VBox(status, Panel(log, "Win32 visibility and synchronous cleanup timings"))) as app:
                app.call_later(.02, tick, repeat=True)
                app.run()
            if report["result"] == "running":
                report["result"] = "cancelled"
    except Exception as error:
        report.update(result="failed", error=repr(error))
        raise
    finally:
        if workspace:
            workspace.close()
        if owner:
            owner.close()
        save()


if __name__ == "__main__":
    main()
