"""Reproducible CPU/encoder benchmark. Does not measure terminal presentation."""
import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from comfortable_tui import App, ListView, Panel, Renderer


def benchmark(width, height, count, frames, native):
    view = ListView([f"Track {i:05d} / benchmark" for i in range(count)], item_height=2)
    with App(Panel(view, "Virtualized list")) as app:
        renderer = Renderer(native=native)
        renderer.encode(app.render(width, height, dt=2))
        times, payloads = [], []
        for i in range(frames + 20):
            if i % 6 == 0:
                view.select((view.selected + 1) % count)
            start = time.perf_counter()
            data = renderer.encode(app.render(width, height, dt=1 / 120))
            elapsed = (time.perf_counter() - start) * 1000
            if i >= 20:
                times.append(elapsed)
                payloads.append(len(data.encode()))
        view.select(view.selected)
        for _ in range(10):
            frame = app.render(width, height, dt=1)
        renderer.encode(frame)
        idle = len(renderer.encode(app.render(width, height, dt=1)).encode())
    return {"size": [width, height], "items": count, "frames": frames,
            "backend": renderer.backend, "median_ms": round(statistics.median(times), 3),
            "p95_ms": round(sorted(times)[min(len(times) - 1, int(len(times) * .95))], 3),
            "mean_bytes": round(statistics.mean(payloads)), "idle_bytes": idle}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")
    results = {"python": platform.python_version(), "platform": platform.platform(),
               "workload": "headless list layout + draw + ANSI encode; no terminal I/O",
               "results": [benchmark(w, h, n, args.frames, native)
                           for native in (False, True) for w, h, n in
                           [(80, 24, 1000), (120, 40, 10_000), (200, 60, 10_000)]]}
    output = json.dumps(results, indent=2)
    print(output)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
