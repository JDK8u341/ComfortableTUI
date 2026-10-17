"""One background sampler; process CPU is delta(user+system)/wall time.

No blocking cpu_percent interval and no expensive per-process maps/connections.
PID + creation time distinguishes process reuse. Inaccessible fields stay None.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import time

from .model import Process, Sample


def counter_rate(previous, current, elapsed):
    if previous is None or current is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / elapsed


def cpu_pair(value):
    fields = value._asdict()
    total = sum(fields.values()) - fields.get("guest", 0) - fields.get("guest_nice", 0)
    idle = fields.get("idle", 0) + fields.get("iowait", 0)
    return total, total - idle


def cpu_load(before, after):
    total = after[0] - before[0]
    busy = after[1] - before[1]
    return min(100., max(0., busy * 100 / total)) if total > 0 and busy >= 0 else None


class Collector:
    def __init__(self, psutil_module=None):
        if psutil_module is None:
            import psutil as psutil_module
        self.ps = psutil_module
        self.windows = os.name == "nt" and getattr(self.ps, "WINDOWS", False)
        self.users = {}
        self.previous_time = None
        self.previous_cpu = []
        self.previous_processes = {}
        self.previous_net = {}
        self.previous_disk = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="comfortable-top-sample")
        self.future = None

    def request(self):
        if self.future is None:
            self.future = self.executor.submit(self.collect)
            return True
        return False

    def poll(self):
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            return future.result()
        return None

    def _counters(self, function, key):
        try:
            return function(**{key: True}) or {}
        except (OSError, NotImplementedError, self.ps.Error):
            return {}

    def collect(self):
        start = time.perf_counter()
        elapsed = start - self.previous_time if self.previous_time is not None else 0
        cpu_now = [cpu_pair(v) for v in self.ps.cpu_times(percpu=True)]
        cores = [cpu_load(a, b) for a, b in zip(self.previous_cpu, cpu_now)]
        cpu = (sum(v for v in cores if v is not None) / len(cores)
               if cores and all(v is not None for v in cores) and len(cores) == len(cpu_now) else None)
        memory = self.ps.virtual_memory()
        # Windows swap_percent can itself enumerate the entire system and take
        # ~1 second. Leave it unavailable instead of blocking every sample.
        swap = self.ps.swap_memory().percent if not self.windows else None
        net = self._counters(self.ps.net_io_counters, "pernic")
        disks = self._counters(self.ps.disk_io_counters, "perdisk")
        net_rates, disk_rates = [], []
        for name, counters in net.items():
            old = self.previous_net.get(name)
            net_rates.append((name, counter_rate(old.bytes_recv if old else None, counters.bytes_recv, elapsed),
                              counter_rate(old.bytes_sent if old else None, counters.bytes_sent, elapsed)))
        for name, counters in disks.items():
            old = self.previous_disk.get(name)
            disk_rates.append((name, counter_rate(old.read_bytes if old else None, counters.read_bytes, elapsed),
                               counter_rate(old.write_bytes if old else None, counters.write_bytes, elapsed)))
        rows, next_processes, denied = [], {}, 0
        attrs = ["pid", "ppid", "name", "username", "status", "create_time", "cpu_times", "memory_info", "num_threads"]
        for info in self._records(attrs):
            try:
                # Without creation time PID reuse cannot be distinguished.
                if info["create_time"] is None:
                    denied += 1
                    continue
                key = info["pid"], info["create_time"]
                times = info["cpu_times"]
                total = times.user + times.system if times else None
                previous = self.previous_processes.get(key)
                usage = counter_rate(previous, total, elapsed)
                if usage is not None:
                    usage *= 100
                next_processes[key] = total
                rss = info["memory_info"].rss if info["memory_info"] else None
                denied += int(any(info[k] is None for k in (*attrs, "memory_info", "num_threads")))
                rows.append(Process(info["pid"], info["create_time"], info["ppid"] or 0,
                    info["name"] or "<unavailable>", info["username"] or "<unavailable>",
                    str(info["status"] or "<unavailable>"), usage, rss, info["num_threads"]))
            except (self.ps.NoSuchProcess, self.ps.AccessDenied, self.ps.ZombieProcess):
                denied += 1
        self.previous_time, self.previous_cpu = start, cpu_now
        self.previous_processes, self.previous_net, self.previous_disk = next_processes, net, disks
        self.users = {key: value for key, value in self.users.items() if key in next_processes}

        def aggregate(rates, index):
            values = [r[index] for r in rates]
            return sum(values) if values and all(v is not None for v in values) else None

        return Sample(time.time(), elapsed, cpu, tuple(cores),
            memory.percent, memory.total - memory.available, memory.total, swap,
            aggregate(net_rates, 1), aggregate(net_rates, 2), aggregate(disk_rates, 1), aggregate(disk_rates, 2),
            max(0, time.time() - self.ps.boot_time()), tuple(rows), denied,
            (time.perf_counter() - start) * 1000, tuple(net_rates), tuple(disk_rates))

    def _records(self, attrs):
        if not self.windows:
            for process in self.ps.process_iter(attrs=attrs, ad_value=None):
                yield process.info
            return
        from .windows import process_records
        for info in process_records():
            key = info["pid"], info["create_time"]
            if info["create_time"] is not None and key not in self.users:
                try:
                    self.users[key] = self.ps.Process(info["pid"]).username()
                except self.ps.Error:
                    self.users[key] = None
            info["username"] = self.users.get(key)
            yield info

    def details(self, key):
        """Called explicitly for one selected process, with identity recheck."""
        process = self.ps.Process(key[0])
        if abs(process.create_time() - key[1]) > .000001:
            raise RuntimeError("The process exited and its PID was reused")
        return process.as_dict(attrs=["pid", "name", "exe", "cmdline", "cwd", "username", "num_threads"],
                               ad_value="<unavailable>")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
