"""Verify this example's C++ producer without any desktop capture."""
import argparse
import json
from pathlib import Path
import sys
import time

from .rendering import Chart


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canvas", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--native-module", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = {"canvas": args.canvas, "checks": []}
    chart = Chart("native", gpu=args.canvas == "gpu", module_dir=args.native_module)
    try:
        chart.set_values([5, 95, 10, 85, 20, 75])
        previous = sys.getswitchinterval()
        try:
            sys.setswitchinterval(1)
            before = chart.provider.frames
            deadline = time.perf_counter() + .35
            while time.perf_counter() < deadline:
                pass
            advanced = chart.provider.frames - before
        finally:
            sys.setswitchinterval(previous)
        assert advanced >= 2 and not chart.provider.error
        result["gil_held_frames"] = advanced
        result["checks"].append("native_producer_advances_while_python_holds_gil")
        time.sleep(.8)
        before = chart.provider.frames
        time.sleep(.2)
        assert chart.provider.frames == before
        result["checks"].append("settled_chart_does_not_republish_unchanged_pixels")
        if args.canvas == "cpu":
            with chart.source.read() as lease:
                assert lease is not None
                assert len(set(bytes(lease.pixels))) > 5
            result["checks"].append("cpp_cpu_frame_readable_via_public_sdk")
        try:
            chart.provider.set_samples([float("nan")])
        except ValueError:
            result["checks"].append("invalid_sample_rejected")
        else:
            raise AssertionError("non-finite sample accepted")
        result["result"] = "passed"
    finally:
        chart.close()
        chart.provider.close()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
