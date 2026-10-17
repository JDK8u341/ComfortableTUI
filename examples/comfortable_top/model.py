"""Immutable samples and bounded history; independent of GUI/psutil/CEF."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import time

Identity = tuple[int, float]


@dataclass(frozen=True)
class Process:
    pid: int
    created: float
    ppid: int
    name: str
    user: str
    status: str
    cpu: float | None
    rss: int | None
    threads: int | None

    @property
    def key(self) -> Identity:
        return self.pid, self.created


@dataclass(frozen=True)
class Sample:
    timestamp: float
    elapsed: float
    cpu: float | None
    cores: tuple[float | None, ...]
    memory_percent: float
    memory_used: int
    memory_total: int
    swap_percent: float | None
    recv: float | None
    sent: float | None
    read: float | None
    write: float | None
    uptime: float
    processes: tuple[Process, ...]
    denied: int = 0
    collection_ms: float = 0
    interfaces: tuple[tuple[str, float | None, float | None], ...] = ()
    disks: tuple[tuple[str, float | None, float | None], ...] = ()


def human_bytes(value, *, rate=False):
    if value is None:
        return "n/a"
    unit = "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            break
        value /= 1024
    return f"{value:.1f}{unit}" + ("/s" if rate else "")


def percent(value):
    return "warmup" if value is None else f"{value:.1f}%"


@dataclass
class MonitorModel:
    limit: int = 180
    sample: Sample | None = None
    selected: Identity | None = None
    query: str = ""
    sort: str = "CPU"
    descending: bool = True
    own_user: str | None = None
    pins: list[Identity] = field(default_factory=list)
    history: dict[Identity, deque] = field(default_factory=dict)
    timeline: deque = field(default_factory=lambda: deque(maxlen=180))

    def accept(self, sample):
        self.sample = sample
        self.timeline.append((sample.timestamp, sample.cpu, sample.memory_percent, sample.recv, sample.sent))
        current = {p.key for p in sample.processes}
        for key in list(self.history):
            if key not in current and key not in self.pins:
                del self.history[key]
        for process in sample.processes:
            series = self.history.setdefault(process.key, deque(maxlen=self.limit))
            series.append(process.cpu)

    def rows(self):
        if self.sample is None:
            return []
        terms = self.query.casefold().split()
        rows = [p for p in self.sample.processes
                if (not self.own_user or p.user == self.own_user) and
                all(term in f"{p.pid} {p.name} {p.user} {p.status}".casefold() for term in terms)]
        field_name = {"CPU": "cpu", "Memory": "rss", "PID": "pid", "Name": "name"}[self.sort]
        # Unknown readings sort last in either direction. PID breaks ties
        # deterministically without treating a reused PID as the same process.
        known = [p for p in rows if getattr(p, field_name) is not None]
        unknown = [p for p in rows if getattr(p, field_name) is None]
        known.sort(key=lambda p: (getattr(p, field_name), p.pid), reverse=self.descending)
        return known + sorted(unknown, key=lambda p: p.pid)

    def process(self, key=None):
        key = self.selected if key is None else key
        return next((p for p in self.sample.processes if p.key == key), None) if self.sample else None

    def pin(self, key):
        if key in self.pins:
            self.pins.remove(key)
        elif len(self.pins) < 4 and self.process(key):
            self.pins.append(key)
        else:
            return False
        return True

    def payload(self):
        """Only bounded telemetry goes to a local webpage; no process cmdline."""
        s = self.sample
        return dict(timestamp=s.timestamp if s else None, cpu=s.cpu if s else None,
                    memory=s.memory_percent if s else None,
                    history=list(self.timeline), selected=asdict(self.process()) if self.process() else None,
                    processes=[asdict(p) for p in self.rows()[:40]])


def export_snapshot(path, sample, notes, fields):
    """User-triggered export, exclusive creation so existing files survive."""
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    data = {"format": "comfortable-top/1", "exported": time.time(), "notes": notes}
    if sample:
        if 0 in fields:
            data["system"] = {k: v for k, v in asdict(sample).items() if k != "processes"}
        if 1 in fields:
            data["processes"] = [asdict(p) for p in sample.processes]
    filename = target / f"comfortable-top-{time.time_ns()}.json"
    with filename.open("x", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
    return str(filename)
