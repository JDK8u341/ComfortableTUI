"""Hybrid monitor UI: real metrics, identity-stable rows and bounded views."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import time

from comfortable_tui import (App, BarChart, Button, Checkbox, Collapsible, DataTable, DirectoryTree,
    Grid, HBox, Input, Label, ListView, Log, PageStack, Panel, Paragraph, ProgressBar, RadioGroup,
    Rule, ScrollView, Select, SelectionList, Slider, Sparkline, Spinner, Switch, Tabs, Text,
    TextArea, Tree, TreeNode, VBox)

from .model import MonitorModel, export_snapshot, human_bytes, percent


class HistorySparkline(Sparkline):
    def __init__(self, model, key, **kwargs):
        self.model, self.key = model, key
        super().__init__(**kwargs)

    def paint(self, canvas):
        self.samples = [v for v in self.model.history.get(self.key, ()) if v is not None]
        super().paint(canvas)


class ResponsiveSplit(HBox):
    def layout(self, rect):
        self.children[-1].visible = rect.width >= 100
        super().layout(rect)


class ProcessTable(DataTable):
    def __init__(self, monitor):
        self.monitor, self.records, self.updating = monitor, [], False
        super().__init__(["PID", "Name", "CPU%", "RSS", "User", "State", "Trend"],
            column_widths=[7, 20, 8, 11, 16, 11, 12], cell_widget=self.cell,
            on_change=self.changed, on_select=lambda i, row: monitor.inspect())

    def cell(self, row, col, text):
        if col == 6:
            return HistorySparkline(self.monitor.model, self.records[row].key)

    def changed(self, index):
        if not self.updating and 0 <= index < len(self.records):
            self.monitor.model.selected = self.records[index].key
            self.monitor.update_details()

    def refresh(self):
        records = self.monitor.model.rows()
        selected = self.monitor.model.selected
        old_index = self.selected
        self.records = records
        self.updating = True
        try:
            self.set_rows([(p.pid, p.name, percent(p.cpu), human_bytes(p.rss), p.user, p.status, "") for p in records])
            index = next((i for i, p in enumerate(records) if p.key == selected), min(old_index, len(records) - 1))
            self.select(max(0, index))
            # Do not lock onto an arbitrary PID while every CPU value is still
            # warming up; the first valid CPU sort should open at its top row.
            warming = self.monitor.model.sample is not None and self.monitor.model.sample.cpu is None
            self.monitor.model.selected = records[self.selected].key if records and (selected or not warming) else None
        finally:
            self.updating = False


class ProcessTree(Tree):
    def __init__(self, monitor):
        self.monitor = monitor
        super().__init__(TreeNode("Processes", expanded=True), on_select=self.chosen)

    def chosen(self, index, text):
        node, _ = self.nodes[index]
        if node.data:
            self.monitor.choose(node.data)

    def update_processes(self, processes):
        expanded = {node.data for node, _ in self.nodes if node.expanded}
        selected = self.nodes[self.selected][0].data if self.nodes else None
        nodes = {p.pid: TreeNode(f"{p.pid} {p.name}  {percent(p.cpu)}", data=p.key,
                                expanded=p.key in expanded) for p in processes}
        root = TreeNode(f"Processes · {len(nodes)}", expanded=True)
        # Parent graphs can be stale or cyclic during collection. Bound a path
        # check before adding each edge; never recurse on untrusted topology.
        parents = {p.pid: p.ppid for p in processes}
        for p in processes:
            seen, ancestor = {p.pid}, p.ppid
            while ancestor in parents and ancestor not in seen:
                seen.add(ancestor)
                ancestor = parents[ancestor]
            parent = nodes.get(p.ppid) if ancestor != p.pid and p.ppid != p.pid else None
            (parent.children if parent else root.children).append(nodes[p.pid])
        self.root = root
        self.refresh()
        self.selected = next((i for i, (node, _) in enumerate(self.nodes) if node.data == selected), 0)


class Monitor:
    def __init__(self, collector, charts, *, web=None, interval=1., fps=60, output=None, user=None):
        self.collector, self.charts, self.web = collector, charts, web
        self.model = MonitorModel()
        self.interval, self.user = interval, user
        self.paused, self.last_request, self.failures = False, -float("inf"), 0
        self.count = 0
        self.header = Label("Comfortable Top · waiting for live system data", height=1)
        self.status = Label("F1 help · F2 search · F3 sort · F4 pin · F6 menu · Tab focus · Ctrl+Q quit", height=1)
        self.spinner = Spinner("Collecting", height=1, width=18)
        self.log = Log(max_lines=300)
        self.log.write("Sampler started. Process CPU: 100% = one logical core. No process actions are executed.")
        self.cpu_bar, self.memory_bar = ProgressBar(height=1), ProgressBar(height=1)
        self.cpu_label, self.memory_label = Label("CPU warmup", height=1), Label("RAM", height=1)
        self.io_label = Label("Network/disk rates warmup", height=3)
        self.search = Input(placeholder="Filter PID / name / user", on_change=self.filter, height=1)
        self.sort = Select(["CPU", "Memory", "PID", "Name"], on_change=self.sort_changed, width=15, height=1)
        self.pause = Switch("Pause", False, on_change=self.pause_changed, width=13, height=1)
        self.own = Checkbox("My user", False, on_change=self.own_changed, width=14, height=1)
        self.details = Label("Select a process", height=8)
        self.selected_chart = self.charts.get("selected")
        self.cpu_chart = self.charts.get("cpu")
        self.table = ProcessTable(self)
        sidebar = VBox(self.details, Panel(Text("CPU ", self.selected_chart.widget(width=24, height=5)), "Selected CPU · 0..100%"),
            HBox(Button("Inspect", self.inspect), Button("Pin/unpin", self.pin), height=1, gap=1), gap=1, width=32)
        processes = ResponsiveSplit(Panel(self.table, "Processes · arrows scroll · Enter inspect"), sidebar, gap=1)

        self.core_chart = BarChart({}, height=12)
        self.network = DataTable(["Interface", "Receive/s", "Send/s"], column_widths=[18, 14, 14])
        self.disks = DataTable(["Device", "Read/s", "Write/s"], column_widths=[18, 14, 14])
        resource_graph = web.widget() if web else Paragraph(
            "Use --mode hybrid for interactive browser analytics. Native CPU/GPU charts use --mode native. "
            "This mode already plots the same real metrics using Kitty graphics or braille.")
        resources = VBox(HBox(Panel(self.cpu_chart.widget(), "System CPU · 0..100%"),
            Panel(resource_graph, "Independent browser analytics" if web else "Rendering modes"), gap=1),
            Grid(Panel(self.core_chart, "Logical cores · relative bars / numeric %"),
                 Panel(VBox(self.network, self.disks), "Network and disks"), columns=2), gap=1)

        self.watch = ListView([], item_height=5, item_widget=self.watch_row,
            on_select=lambda i, row: self.choose(self.model.pins[i]))
        self.compare = DataTable(["Watch", "Independent analysis"], [], column_widths=[20, 55], item_height=7,
            cell_widget=self.compare_cell, on_change=self.compare_changed)
        self.tree = ProcessTree(self)
        self.workbench = Tabs({"Watch list": HBox(Panel(self.watch, "Pinned histories · up to four", width=36),
            Panel(self.compare, "Per-process pages · Enter interact / Esc leave"), gap=1),
            "Process tree": Panel(self.tree, "Parent relationships · arrows expand · Enter select")})

        self.notes = TextArea("# Investigation notes\n\nPin a process, inspect it and record what you observe.\n")
        self.output = Input(str(output or Path.cwd() / "top-exports"), height=1)
        self.export_fields = SelectionList(["System + interface counters", "Process snapshot"], checked=[0, 1], height=2)
        self.directory = DirectoryTree(str(Path.cwd()))
        self.pages = PageStack(VBox(Panel(self.notes, "Session notes · not sent to browser"),
            Panel(self.log, "Activity log", height=6),
            HBox(Button("Preview export", self.preview_export), Button("Export JSON", self.export), height=1, gap=1), gap=1))
        workspace = HBox(self.pages, Panel(VBox(Label("Output directory", height=1), self.output,
            self.export_fields, self.directory, gap=1), "Files and export", width=40), gap=1)

        self.cadence = Slider(interval, minimum=.25, maximum=5, step=.25, on_change=self.interval_changed, height=1)
        settings = ScrollView(VBox(Paragraph("Collection cadence is separate from UI FPS. One collection may run at a time.", height=2),
            Label("Sample interval / seconds", height=1), self.cadence,
            RadioGroup(["Descending", "Ascending"], on_change=lambda i: self.order_changed(i), height=2),
            Collapsible("Metric meanings", Paragraph(
                "CPU: process 100% means one core; system 100% means the whole machine. "
                "Unknown or first readings show warmup/n/a. Process RSS is resident memory, not unique memory. "
                "Disk counters are per-device OS totals and can include layered devices. Network totals include loopback.", height=5), height=6),
            Label("Rendering / transport", height=1),
            Paragraph(f"{charts.mode} mode. Charts: {'D3D11 shared GPU' if charts.gpu else 'native shared CPU' if charts.mode != 'terminal' else 'Kitty / braille'}. "
                "CEF, profiles and message topics are an independent optional upper service.", height=3),
            Button("Keyboard help", self.help, height=1), gap=1))
        self.tabs = Tabs({"Processes": processes, "Resources": resources, "Compare": self.workbench,
                          "Workspace": workspace, "Settings": Panel(settings, "Monitor settings")})
        cards = HBox(Panel(VBox(self.cpu_label, self.cpu_bar), "CPU"),
            Panel(VBox(self.memory_label, self.memory_bar), "Memory"), Panel(self.io_label, "Throughput"), height=5, gap=1)
        root = VBox(HBox(self.header, self.spinner, height=1), cards, HBox(self.search, self.sort, self.pause, self.own, height=1, gap=1),
            Rule(height=1), self.tabs, Rule(height=1), self.status, height=None)
        self.app = App(root, fps=fps)
        for key, function in {"f1": self.help, "f2": lambda: self.app.focus(self.search),
            "f3": lambda: self.sort.set_value(self.sort.selected + 1), "f4": self.pin,
            "f7": lambda: self.pause_changed(not self.paused), "f8": self.inspect,
            "ctrl+e": self.export}.items():
            self.app.bind(key, function)
        for i in range(5):
            self.app.bind(f"ctrl+{i + 1}", lambda i=i: self.tabs.select(i))
        if web:
            web.on_select = self.choose
        self.app.watch_events(self.poll)
        self.app.call_later(.1, self.schedule, repeat=True)

    def filter(self, text):
        self.model.query = text
        self.refresh_rows()

    def sort_changed(self, index, name):
        self.model.sort = name
        self.refresh_rows()

    def order_changed(self, index):
        self.model.descending = index == 0
        self.refresh_rows()

    def own_changed(self, checked):
        self.model.own_user = self.user if checked else None
        self.refresh_rows()

    def interval_changed(self, value):
        self.interval = value
        self.log.write(f"Sample interval {value:g}s")

    def pause_changed(self, value):
        self.paused = self.pause.checked = value
        self.pause.invalidate()
        self.log.write("Sampling paused" if value else "Sampling resumed")
        self.app.invalidate()

    def schedule(self):
        now = time.perf_counter()
        if not self.paused and now - self.last_request >= self.interval and self.collector.request():
            self.last_request = now
            self.spinner.running = True

    def poll(self):
        try:
            sample = self.collector.poll()
        except Exception as error:
            self.failures += 1
            self.log.write(f"Sampler error: {error}")
            self.status.set_text(f"Sampler error: {error} · F1 help")
            return
        if sample:
            self.accept(sample)

    def accept(self, sample):
        if self.paused:
            return  # Discard one already-in-flight sample on pause.
        self.model.accept(sample)
        self.count += 1
        self.spinner.running = False
        self.header.set_text(f"Comfortable Top  /  {platform.node()}  /  {platform.system()}  /  "
            f"up {sample.uptime / 3600:.1f}h · {len(sample.processes)} readable processes · {sample.collection_ms:.0f}ms sample")
        self.cpu_label.set_text(f"{percent(sample.cpu)} · {len(sample.cores)} logical CPUs")
        self.memory_label.set_text(f"{human_bytes(sample.memory_used)} / {human_bytes(sample.memory_total)} · "
                                  f"swap {percent(sample.swap_percent) if sample.swap_percent is not None else 'n/a'}")
        if sample.cpu is not None:
            self.cpu_bar.set_value(sample.cpu)
        self.memory_bar.set_value(sample.memory_percent)
        self.io_label.set_text(f"↓ {human_bytes(sample.recv, rate=True)}   ↑ {human_bytes(sample.sent, rate=True)}\n"
            f"R {human_bytes(sample.read, rate=True)}   W {human_bytes(sample.write, rate=True)}\n"
            f"{sample.denied} partial/unavailable process records")
        self.core_chart.samples = {f"CPU {i}": round(v, 1) for i, v in enumerate(sample.cores) if v is not None}
        self.network.set_rows([(n, human_bytes(a, rate=True), human_bytes(b, rate=True)) for n, a, b in sample.interfaces])
        self.disks.set_rows([(n, human_bytes(a, rate=True), human_bytes(b, rate=True)) for n, a, b in sample.disks])
        self.cpu_chart.set_values(row[1] for row in self.model.timeline)
        self.refresh_rows()
        self.tree.update_processes(sample.processes)
        for slot, key in enumerate(self.model.pins):
            self.charts.get(f"watch-{slot}").set_values(self.model.history.get(key, ()))
        for i, row in self.watch._row_widgets.items():
            if i < len(self.model.pins):
                row.children[0].set_text(self.watch_text(self.model.pins[i]))
        if self.web:
            self.web.publish(self.model)
        self.app.invalidate()

    def refresh_rows(self):
        if hasattr(self, "table"):
            self.table.refresh()
            self.update_details()

    def update_details(self):
        p = self.model.process()
        if p:
            self.details.set_text(f"{p.name}\nPID {p.pid} · parent {p.ppid}\n{p.user}\n{p.status}\n"
                f"CPU {percent(p.cpu)}\nRSS {human_bytes(p.rss)}\nThreads {p.threads if p.threads is not None else 'n/a'}\n"
                f"{'Pinned' if p.key in self.model.pins else 'F4 to pin'}")
            self.selected_chart.set_values(self.model.history.get(p.key, ()))
        else:
            self.details.set_text("No matching process")
            self.selected_chart.set_values(())
        if hasattr(self, "app"):
            self.app.invalidate()

    def choose(self, key):
        if not self.model.process(key):
            self.app.notify("Process already exited")
            return
        self.model.selected = key
        self.search.set_value("")
        self.tabs.select(0)
        self.table.refresh()
        self.update_details()
        self.app.focus(self.table)

    def watch_text(self, key):
        p = self.model.process(key)
        return f"{p.pid} {p.name}\n{percent(p.cpu)} · {human_bytes(p.rss)}" if p else f"{key[0]} · exited\nF4 to unpin"

    def watch_row(self, index, text):
        return HBox(Label(self.watch_text(self.model.pins[index]), width=20),
                    self.charts.get(f"watch-{index}").widget(), gap=1)

    def compare_cell(self, row, col, value):
        if col == 1:
            key = self.model.pins[row]
            return self.web.widget(key) if self.web else self.charts.get(f"watch-{row}").widget()

    def compare_changed(self, index):
        if index < len(self.model.pins):
            self.model.selected = self.model.pins[index]
            self.update_details()

    def pin(self):
        # On a watch row F4 unpins even after the process has exited.
        key = self.model.selected
        child = self.app.focused
        while child and child.parent not in (self.watch, self.compare):
            child = child.parent
        if child and child.parent in (self.watch, self.compare):
            container = child.parent
            index = next((i for i, row in container._row_widgets.items() if row is child), container.selected)
            if index < len(self.model.pins):
                key = self.model.pins[index]
        elif self.app.focused in (self.watch, self.compare) and self.model.pins:
            key = self.model.pins[self.app.focused.selected]
        if key is None or not self.model.pin(key):
            self.app.notify("Select a live process; at most four watches")
            return
        self.watch.set_items([str(k[0]) for k in self.model.pins])
        self.compare.set_rows([(k[0], "") for k in self.model.pins])
        if self.web:
            for old in list(self.web.pages):
                if old != "overview" and old not in self.model.pins:
                    self.web.pages.pop(old).close()
        self.log.write(f"Watch set: {[k[0] for k in self.model.pins]}")
        self.update_details()
        for slot, identity in enumerate(self.model.pins):
            self.charts.get(f"watch-{slot}").set_values(self.model.history.get(identity, ()))
        if self.web:
            self.web.publish(self.model)

    def inspect(self):
        key = self.model.selected
        if key is None:
            return
        self.app.run_worker(lambda: self.collector.details(key), self.show_details,
                            on_error=lambda error: self.app.notify(str(error)))

    def show_details(self, details):
        body = json.dumps(details, indent=2, ensure_ascii=False)
        self.app.show_modal(Panel(VBox(ScrollView(Paragraph(body)),
            Button("Close", self.app.close_modal, height=1), gap=1), "Process detail · Esc closes", width=88, height=22))

    def preview_export(self):
        sample = self.model.sample
        preview = f"Processes: {len(sample.processes) if sample else 0}\nDirectory: {self.output.text}\n\n{self.notes.text}"
        self.pages.push(Panel(VBox(ScrollView(Paragraph(preview)),
            HBox(Button("Back", self.pages.pop), Button("Export", self.export), height=1)), "Export preview"))

    def export(self):
        path, sample, notes, fields = self.output.text, self.model.sample, self.notes.text, set(self.export_fields.checked)
        self.app.run_worker(lambda: export_snapshot(path, sample, notes, fields),
            lambda filename: (self.log.write(f"Exported {filename}"), self.app.notify(f"Saved {filename}")),
            on_error=lambda error: self.app.notify(f"Export failed: {error}"))

    def help(self):
        content = VBox(Paragraph(
            "F2 search · F3 cycle sort · F4 pin/unpin · F7 pause sampling · F8 process detail\n"
            "Ctrl+1..5 pages · Ctrl+E export · F6 menu/content · Tab/Shift+Tab focus\n"
            "Arrows navigate/edit · Enter inspect row or enter embedded browser · Esc leave browser/close modal\n\n"
            "Pin up to four identities in Processes, then open Compare. Each browser analysis is a separate page; "
            "they exchange only explicit telemetry and selection messages. View ranges are independent.\n\n"
            "CPU warmup is intentional. Process CPU may exceed 100%; plots clip to 100%. "
            "Filtering/sorting follows identity, not row number. Sampling never overlaps.\n\n"
            "Notes stay local and are included only in explicit exports.", height=14),
            Button("Close", self.app.close_modal, height=1), gap=1)
        self.app.show_modal(Panel(content, "Comfortable Top / help", width=88, height=22))
