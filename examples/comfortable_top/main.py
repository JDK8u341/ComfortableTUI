"""Comfortable Top: live process monitor with optional native/browser layers."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import sys
from pathlib import Path
import time

from .collector import Collector
from .rendering import Charts
from .ui import Monitor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("terminal", "native", "hybrid"), default="terminal")
    parser.add_argument("--canvas", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--platform", choices=("x11", "wayland"))
    parser.add_argument("--native-module", type=Path)
    parser.add_argument("--interval", type=float, default=1.)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--output", type=Path, help="Directory for user-triggered JSON exports")
    parser.add_argument("--snapshot", action="store_true", help="Collect twice and print a real terminal frame")
    parser.add_argument("--smoke-seconds", type=float, default=0)
    parser.add_argument("--report", type=Path, help="Write aggregate validation results, without process names")
    parser.add_argument("--self-test", action="store_true", help="Exercise filter/pin/tabs/browser interaction on own UI")
    args = parser.parse_args()
    if args.snapshot and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not .25 <= args.interval <= 5 or not 1 <= args.fps <= 240:
        parser.error("interval must be .25..5, fps 1..240")
    if args.canvas == "gpu" and (os.name != "nt" or args.mode == "terminal"):
        parser.error("shared GPU charts require Windows --mode native or hybrid")
    if args.snapshot and args.mode != "terminal":
        parser.error("--snapshot uses terminal mode; native surfaces need a real display")
    if os.name != "nt" and args.mode != "terminal" and not args.platform:
        parser.error("select --platform x11 or --platform wayland for native/hybrid")
    try:
        import psutil
    except ImportError:
        parser.error('install the monitor dependency: python -m pip install -e ".[monitor]"')

    report = {"mode": args.mode, "canvas": args.canvas, "platform": args.platform, "checks": []}
    try:
        with ExitStack() as stack:
            charts = Charts(args.mode, gpu=args.canvas == "gpu", module_dir=args.native_module)
            stack.callback(charts.close)
            web = None
            if args.mode == "hybrid":
                from .web import WebPanels
                web = WebPanels(args.platform)
                stack.callback(web.close)
            collector = Collector(psutil)
            stack.callback(collector.close)
            monitor = Monitor(collector, charts, web=web, interval=args.interval, fps=args.fps,
                              output=args.output, user=psutil.Process().username())
            stack.enter_context(monitor.app)
            if args.snapshot:
                collector.request()
                while (sample := collector.poll()) is None:
                    time.sleep(.01)
                monitor.accept(sample)
                time.sleep(max(.25, args.interval))
                collector.request()
                while (sample := collector.poll()) is None:
                    time.sleep(.01)
                monitor.accept(sample)
                print(monitor.app.render(140, 42, dt=1).plain())
                report["checks"].append("two_live_samples_and_terminal_frame")
            else:
                if args.self_test:
                    from .verification import install_checks
                    install_checks(monitor, report)
                if args.smoke_seconds:
                    monitor.app.call_later(args.smoke_seconds, monitor.app.exit)
                if os.name == "nt" or args.mode == "terminal":
                    monitor.app.run()
                else:
                    from comfortable_tui import WindowHost
                    with WindowHost(monitor.app, display_platform=args.platform) as host:
                        report["host"] = host.capabilities
                        host.run()
            report.update(samples=monitor.count, processes=len(monitor.model.sample.processes) if monitor.model.sample else 0,
                sampler_errors=monitor.failures,
                native_frames={str(k): v.provider.frames for k, v in charts.items.items() if v.provider},
                native_errors={str(k): v.provider.error for k, v in charts.items.items() if v.provider and v.provider.error},
                canvas_events={str(k): dict(v.events) for k, v in charts.items.items() if v.events},
                browser_pages=len(web.pages) if web else 0)
            if report["native_errors"] or monitor.failures:
                raise RuntimeError("Sampler or native renderer reported an error")
            if args.self_test and len(report["checks"]) < (7 if web else 6):
                raise RuntimeError("Self-test did not finish; allow at least 14 smoke seconds")
            report["result"] = "passed"
    except BaseException as error:
        report.update(result="failed", error=str(error))
        raise
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
