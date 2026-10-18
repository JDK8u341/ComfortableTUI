"""Fixed ComfortableTop 0.1.2 fixture: compare ANSI hashes across environments."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--top', type=Path, required=True, help='ComfortableTop 0.1.2 source checkout')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--clock', choices=('wall', 'thread'), default='wall',
                    help='Thread CPU time isolates Python rendering cost from OS preemption')
args = parser.parse_args()
TOP = args.top.resolve()
sys.path.insert(0, str(TOP / 'tests'))
from test_monitor import FakePsutil, process, sample  # noqa: E402
from comfortable_top.collector import Collector  # noqa: E402
from comfortable_top.rendering import Charts  # noqa: E402
from comfortable_top.ui import Monitor  # noqa: E402
from comfortable_tui import PixelCanvas  # noqa: E402

collector, charts = Collector(FakePsutil()), Charts('terminal')
monitor = Monitor(collector, charts, language='en', user='fixture')
stream = hashlib.sha256()
total, settled = 0, 0
times = []
timer = time.thread_time if args.clock == 'thread' else time.perf_counter
try:
    with monitor.app as app:
        monitor.accept(sample(*[process(10 + i, cpu=float(i)) for i in range(24)]))
        monitor.pause_changed(True)
        for widget in app.root.walk():
            if isinstance(widget, PixelCanvas):
                widget.backend = 'braille'
        batch_started = timer()
        for i in range(420):
            if i == 40:
                app.focus(monitor.sort)
            elif i == 80:
                monitor.tabs.select(1)
            elif i == 140:
                monitor.help()
            elif i == 180:
                app.close_modal()
            elif i == 220:
                monitor.tabs.select(0)
            before = timer()
            frame = app.render(120, 33, dt=1 / 60)
            output = app.encode(frame).encode('utf-8')
            times.append((timer() - before) * 1000)
            total += len(output)
            stream.update(output)
            if i >= 330:
                settled += len(output)
        batch_ms = (timer() - batch_started) * 1000
        result = dict(result='passed', frames=420, clock=args.clock, total_bytes=total,
            ansi_sha256=stream.hexdigest(), settled_last_90_bytes=settled,
            batch_ms=round(batch_ms, 3), batch_per_frame_ms=round(batch_ms / 420, 3))
        if args.clock == 'wall':
            result.update(cpu_p50_ms=round(statistics.median(times), 3),
                          cpu_p95_ms=round(sorted(times)[int((len(times)-1)*.95)], 3))
        assert settled == 0
finally:
    collector.close()
    charts.close()
args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
