"""Windows latency comparison: identical native CPU source, then a shared-GPU source.

Measures source timestamp to Qt paint completion / D3D11 Present return.
Neither endpoint is photon latency. No screenshot or user input is collected.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import os
import platform
import statistics
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path, default=Path(".research/gpu-build/Release"))
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--source", default="1280x720")
    parser.add_argument("--fps", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "nt" or args.seconds < 1:
        parser.error("requires Windows desktop and --seconds >= 1")
    from PySide6.QtWidgets import QApplication
    from comfortable_tui import FrameBuffer, GpuFrameSource, PixelRect, Rect
    from comfortable_tui._overlay_qt import _make_layer_class
    from comfortable_tui._win32_host import Win32
    from comfortable_tui import _native_gpu
    app = QApplication([])
    if app.platformName() != "windows":
        parser.error("requires QT_QPA_PLATFORM=windows")
    width, height = map(int, args.source.split("x"))
    api = Win32()
    results = []
    for mode in ("qt_cpu", "native_cpu", "native_gpu"):
        cpu = FrameBuffer(width, height, pixel_format="bgra8_premultiplied") if mode != "native_gpu" else None
        if cpu:
            command = [str((args.bin / "ctui_cpu_producer.exe").resolve()), cpu.info.name, str(width), str(height),
                       str(cpu.info.stride), str(args.fps), "30"]
        else:
            command = [str((args.bin / "ctui_gpu_producer.exe").resolve()), "--width", str(width), "--height", str(height),
                       "--fps", str(args.fps), "--seconds", "30"]
        producer = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
        layer = None
        try:
            handshake = producer.stdout.readline().split()
            if not handshake:
                raise RuntimeError(producer.stderr.read())
            source = cpu or GpuFrameSource.attach(handshake[1])
            layer = _make_layer_class()(lambda e: None, native=mode != "qt_cpu")
            layer.api = api
            spec = {"id": 1, "buffer": source.info, "placement": (Rect(0, 0, 80, 40), Rect(0, 0, 80, 40)),
                    "fit": "contain", "smooth": True, "input_mode": "capture", "enabled": True, "focused": False,
                    "ime": {"cursor": (0, 0, 1, 1), "text": "", "position": 0, "anchor": 0}, "fps": 120}
            layer.winId()
            layer.apply({"grid": (80, 40), "items": [spec]}, PixelRect(24, 24, 960, 540))
            layer.show()
            start = time.perf_counter()
            last, samples, sequences = -1, [], []
            while time.perf_counter() - start < args.seconds + .5:
                app.processEvents()
                layer.animate()
                if layer.renderer:
                    data = layer.renderer.metrics()
                    if data["error"]:
                        raise RuntimeError(data["error"])
                    views = data["views"]
                    sequence = views[0]["sequence"] if views else 0
                    age = views[0]["frame_age_ms"] if views else 0
                else:
                    sequence, age = layer.items[1]._sequence, layer.items[1]._age_ms
                if time.perf_counter() - start > .5 and sequence and sequence != last:
                    samples.append(age)
                    sequences.append(sequence)
                    last = sequence
                time.sleep(.004)  # Same GUI/control cadence as the shipped Qt helper.
            if not samples:
                raise RuntimeError(f"no frames measured: {mode}")
            results.append({"mode": mode, "samples": len(samples), "source_sequence_delta": sequences[-1] - sequences[0],
                            "median_ms": statistics.median(samples),
                            "p95_ms": sorted(samples)[min(len(samples) - 1, int(len(samples) * .95))],
                            "max_ms": max(samples),
                            "endpoint": "Qt paint complete" if mode == "qt_cpu" else "DXGI Present return"})
        finally:
            if layer:
                layer.shutdown()
                app.processEvents()
            if producer.poll() is None:
                producer.terminate()
            producer.communicate(timeout=5)
            if cpu:
                cpu.close()
    report = {"platform": platform.platform(), "source_pixels": [width, height], "source_fps": args.fps,
              "display_pixels": [960, 540], "seconds": args.seconds, "adapters": _native_gpu.adapters(),
              "note": "submission/paint age, not input-to-photon or DWM scanout; sampled only unique frames after warmup",
              "results": results}
    output = json.dumps(report, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
