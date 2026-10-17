"""Monitor correctness: identity, counter semantics and hybrid composition."""
from collections import namedtuple
from dataclasses import replace
import json
import time
from types import SimpleNamespace

import pytest

from examples.comfortable_top.collector import Collector, counter_rate, cpu_load, cpu_pair
from examples.comfortable_top.model import MonitorModel, Process, Sample, export_snapshot
from examples.comfortable_top.rendering import Charts
from examples.comfortable_top.ui import Monitor
from comfortable_tui import Key


def process(pid=10, created=1., cpu=25., name="python"):
    return Process(pid, created, 1, name, "tester", "running", cpu, 1000000, 2)


def sample(*processes, timestamp=10):
    return Sample(timestamp, 1., 42., (20., 64.), 60., 600, 1000, 0., 100., 20., 30., 40., 1000.,
                  tuple(processes))


def test_counter_warmup_reset_and_cpu_guest_accounting():
    assert counter_rate(None, 10, 1) is None
    assert counter_rate(10, 9, 1) is None
    assert counter_rate(10, 20, 0) is None
    assert counter_rate(10, 20, 2) == 5
    Times = namedtuple("Times", "user system idle iowait guest guest_nice")
    assert cpu_pair(Times(20, 10, 60, 10, 5, 0)) == (100, 30)
    assert cpu_load((100, 20), (200, 70)) == 50
    assert cpu_load((100, 20), (100, 20)) is None


def test_model_tracks_pid_reuse_filters_unknown_sort_and_prunes():
    model = MonitorModel(limit=3)
    old = process()
    model.accept(sample(old, process(20, cpu=None)))
    model.selected = old.key
    assert model.pin(old.key)
    for _ in range(5):
        model.accept(sample(old))
    assert len(model.history[old.key]) == 3
    replacement = process(created=2.)
    model.accept(sample(replacement, process(20, cpu=None)))
    assert model.process(old.key) is None
    assert model.process(replacement.key) is replacement
    assert old.key in model.history  # A pinned exited identity remains explicit.
    model.descending = False
    assert model.rows()[-1].cpu is None
    model.query = "PYTHON 10"
    assert model.rows() == [replacement]
    model.pin(old.key)
    model.accept(sample(replacement))
    assert old.key not in model.history


def test_export_is_explicit_and_does_not_overwrite(tmp_path):
    s = sample(process())
    one = export_snapshot(tmp_path, s, "调查记录", {0})
    two = export_snapshot(tmp_path, s, "notes", {1})
    assert one != two
    a = json.loads(open(one, encoding="utf-8").read())
    b = json.loads(open(two, encoding="utf-8").read())
    assert a["notes"] == "调查记录" and "processes" not in a
    assert "system" not in b and b["processes"][0]["pid"] == 10


class FakePsutil:
    WINDOWS = False
    Error = RuntimeError
    NoSuchProcess = RuntimeError
    AccessDenied = RuntimeError
    ZombieProcess = RuntimeError

    def __init__(self):
        self.tick = 0
        self.created = 1.

    def cpu_times(self, percpu):
        Times = namedtuple("Times", "user system idle")
        return [Times(10 + self.tick, 10, 100 + self.tick)]

    def virtual_memory(self):
        return SimpleNamespace(percent=50., total=1000, available=500)

    def swap_memory(self):
        return SimpleNamespace(percent=0.)

    def net_io_counters(self, pernic):
        return {"eth0": SimpleNamespace(bytes_recv=100 + self.tick * 100, bytes_sent=50 + self.tick * 10)}

    def disk_io_counters(self, perdisk):
        return {}

    def process_iter(self, attrs, ad_value):
        return [SimpleNamespace(info=dict(pid=10, ppid=1, name="python", username="tester", status="running",
            create_time=self.created, cpu_times=SimpleNamespace(user=1 + self.tick, system=0),
            memory_info=SimpleNamespace(rss=1000), num_threads=2))]

    def boot_time(self):
        return time.time() - 100


def test_sampler_first_sample_cpu_and_pid_reuse_are_not_zero(monkeypatch):
    from examples.comfortable_top import collector as module
    ticks = iter([10., 10.01, 12., 12.01, 14., 14.01])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))
    fake = FakePsutil()
    collector = Collector(fake)
    try:
        first = collector.collect()
        assert first.cpu is None and first.recv is None and first.processes[0].cpu is None
        fake.tick = 1
        second = collector.collect()
        assert second.cpu == 50 and second.recv == 50 and second.processes[0].cpu == 50
        assert second.read is None
        fake.created = 2.
        fake.tick = 2
        third = collector.collect()
        assert third.processes[0].cpu is None
    finally:
        collector.close()


def test_only_one_collection_is_in_flight():
    from threading import Event
    entered, finish = Event(), Event()
    collector = Collector(FakePsutil())
    def blocked():
        entered.set()
        finish.wait(5)
        return sample(process())
    collector.collect = blocked
    try:
        assert collector.request()
        assert entered.wait(1)
        assert not collector.request() and collector.poll() is None
        finish.set()
        result = collector.future.result(timeout=2)
        assert collector.poll() is result
        assert collector.future is None
    finally:
        finish.set()
        collector.close()


@pytest.mark.parametrize("size", [(140, 42), (80, 24), (40, 12)])
def test_monitor_composes_pages_preserves_selection_and_original_keys(tmp_path, monkeypatch, size):
    monkeypatch.chdir(tmp_path)
    collector, charts = Collector(FakePsutil()), Charts("terminal")
    monitor = Monitor(collector, charts, output=tmp_path, user="tester")
    try:
        with monitor.app as app:
            monitor.accept(sample(process(), process(20, cpu=80)))
            app.render(*size, dt=1)
            monitor.choose((10, 1.))
            monitor.accept(sample(process(cpu=90), process(20, cpu=5)))
            assert monitor.model.selected == (10, 1.)
            monitor.pin()
            assert monitor.model.pins == [(10, 1.)]
            for index in range(5):
                monitor.tabs.select(index)
                app.render(*size, dt=1)
            monitor.help()
            app.render(*size)
            app.dispatch(Key("escape"))
            monitor.help()  # A modal must not try to reparent an old widget.
            app.close_modal()
            monitor.pause_changed(True)
            count = monitor.count
            monitor.accept(sample(process(), timestamp=11))
            assert monitor.count == count
            monitor.pause_changed(False)
            monitor.filter("missing fixture")
            assert monitor.table.records == []
            monitor.filter("")
            monitor.pages.layout(monitor.tabs.rect)
            monitor.preview_export()
            assert monitor.pages.pop()
            app.render(*size, dt=1)
    finally:
        collector.close()
        charts.close()


def test_windows_native_stats_match_own_process():
    import os
    if os.name != "nt":
        pytest.skip("Win32 only")
    from examples.comfortable_top.windows import process_records
    record = next(p for p in process_records() if p["pid"] == os.getpid())
    assert 0 < time.time() - record["create_time"] < 3600
    assert record["memory_info"].rss > 0 and record["num_threads"] > 0
    assert record["cpu_times"].user >= 0
    # Validate the documented native struct offsets against an independent API.
    psutil = pytest.importorskip("psutil")
    own = psutil.Process()
    assert abs(own.memory_info().rss - record["memory_info"].rss) < 8 * 1024 * 1024
    assert abs(own.create_time() - record["create_time"]) < .000001


def test_process_tree_handles_cycles_and_keeps_every_process_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collector, charts = Collector(FakePsutil()), Charts("terminal")
    monitor = Monitor(collector, charts)
    try:
        rows = [replace(process(10), ppid=20), replace(process(20), ppid=10), replace(process(30), ppid=20)]
        monitor.tree.update_processes(rows)
        pending, keys = [monitor.tree.root], []
        while pending:
            node = pending.pop()
            pending.extend(node.children)
            if node.data:
                keys.append(node.data)
        assert len(keys) == len(set(keys)) == 3
    finally:
        monitor.app.close()
        collector.close()
        charts.close()


def test_first_valid_cpu_sort_starts_at_top_and_nested_focus_unpins_correct_row(tmp_path, monkeypatch):
    from comfortable_tui import Button
    monkeypatch.chdir(tmp_path)
    collector, charts = Collector(FakePsutil()), Charts("terminal")
    monitor = Monitor(collector, charts)
    try:
        with monitor.app as app:
            monitor.accept(replace(sample(process(cpu=None), process(20, cpu=None)), cpu=None))
            assert monitor.model.selected is None
            monitor.accept(sample(process(cpu=10), process(20, cpu=80)))
            assert monitor.model.selected == (20, 1.)
            monitor.pin()
            monitor.model.selected = (10, 1.)
            monitor.pin()
            monitor.compare.cell_widget = lambda r, c, text: Button("Focus") if c == 1 else None
            monitor.tabs.select(2)
            app.render(160, 50, dt=1)
            target = monitor.compare._row_widgets[1].children[1]
            assert app.focus(target)
            monitor.model.selected = (20, 1.)  # Tab focus can differ from row selection.
            monitor.pin()
            assert monitor.model.pins == [(20, 1.)]
    finally:
        collector.close()
        charts.close()
