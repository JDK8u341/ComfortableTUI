"""Launch one dedicated Windows Terminal and await this example's self-test."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("terminal", "native", "hybrid"), default="hybrid")
    parser.add_argument("--canvas", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" or not shutil.which("wt.exe"):
        parser.error("requires an interactive Windows desktop and Windows Terminal")
    root = Path(__file__).resolve().parents[2]
    report = args.report.resolve()
    if report.exists():
        parser.error("choose a new report path; old validation records are not overwritten")
    subprocess.Popen(["wt.exe", "-w", "new", "new-tab", "--title", "Comfortable Top test " + uuid.uuid4().hex[:8],
        "-d", str(root), sys.executable, "-m", "examples.comfortable_top", "--mode", args.mode,
        "--canvas", args.canvas, "--self-test", "--smoke-seconds", "14", "--report", str(report)],
        creationflags=0x08000000)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if report.exists():
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(.05)
                continue
            print(json.dumps(data, indent=2))
            if data.get("result") != "passed":
                raise RuntimeError(data.get("error", "integration test failed"))
            return
        time.sleep(.2)
    raise TimeoutError("Test terminal did not produce its report; its smoke timer closes the app")


if __name__ == "__main__":
    main()
