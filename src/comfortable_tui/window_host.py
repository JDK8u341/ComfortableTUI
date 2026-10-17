"""Optional owned Linux window. Native code reads frames and runs the GUI loop."""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import sys
import tempfile
import time

from ._native_process import NativeProcess
from ._pacing import FramePacer
from ._display_environment import display_environment
from .events import Key, Mouse, Paste
from .gpu import GpuFrameSource
from .overlay import CanvasEvent, CanvasTransform


class WindowHost:
    """Render an App in one owned Linux window, with X11 or native Wayland.

    This mode owns its window and cell text. It does not attach to another
    terminal's window. Frame reads and painting stay in the native process.
    Close this host before closing frame owners or BrowserService.
    """
    def __init__(self, app, *, executable=None, display_platform=None, test_controls=False):
        if sys.platform != "linux":
            raise RuntimeError("WindowHost currently targets Linux; Windows uses App.run() overlays")
        if display_platform not in (None, "x11", "wayland"):
            raise ValueError("display_platform must be x11 or wayland")
        executable = executable or os.environ.get("COMFORTABLE_TUI_LINUX_HOST_EXECUTABLE")
        if not executable:
            executable = Path(__file__).resolve().parents[2] / ".research/host-build/native/host/ctui_linux_canvas"
        if not Path(executable).is_file():
            raise RuntimeError("Build COMFORTABLE_TUI_BUILD_LINUX_HOST=ON and set "
                               "COMFORTABLE_TUI_LINUX_HOST_EXECUTABLE; see docs/webview.md")
        self.app, self.widgets = app, {}
        self._last, self._closed = None, False
        self._log_dir = tempfile.TemporaryDirectory(prefix="ctui-window-host-")
        command = [str(executable)]
        if display_platform:
            command += ["-platform", "xcb" if display_platform == "x11" else "wayland"]
        if test_controls:
            command.append("--test-controls")
        try:
            self._rpc = NativeProcess(command, ready_event="host_ready",
                                      log_path=Path(self._log_dir.name) / "host.log",
                                      env=display_environment(display_platform))
            self.capabilities = self._rpc.capabilities
            actual = self.capabilities["platform"]
            if display_platform and not (actual == "xcb" if display_platform == "x11" else
                                         actual.startswith("wayland")):
                self._rpc.close()
                raise RuntimeError(f"Requested {display_platform}, actual Qt backend is {actual}")
            self.geometry = dict(self.capabilities)
        except BaseException:
            self._log_dir.cleanup()
            raise

    def poll(self):
        if self._closed:
            return
        for event in self._rpc.events():
            kind = event.get("event")
            if kind == "geometry":
                self.geometry.update(event["data"])
                self._last = None
                self.app.invalidate()
            elif kind == "close_requested":
                self.app.exit()
            elif kind in ("error", "helper_exit"):
                raise RuntimeError(event.get("message") or event.get("error", "Window host exited"))
            elif kind == "canvas_event":
                widget = self.widgets.get(event.get("view"))
                if (widget and not widget._closed and widget.app is self.app
                        and widget._event_token == event.get("token")
                        and id(widget) in self.app._overlay_placements):
                    widget._native_event(CanvasEvent(event["kind"], event.get("data", {})))
            elif kind == "tui_event":
                data, input_kind = event["data"], event["kind"]
                if input_kind == "mouse":
                    self.app.dispatch(Mouse(**data))
                elif input_kind == "text":
                    self.app.dispatch(Paste(data["commit"]))
                else:
                    name = " " if data["name"] == "space" else data["name"]
                    self.app.dispatch(Key(name, text=data.get("text", ""),
                        modifiers=frozenset(data.get("modifiers", ())),
                        phase="release" if input_kind == "key_up" else "repeat" if data.get("repeat") else "press"))

    def sync(self, frame):
        """Synchronously acknowledge source replacement before emitting attach/detach."""
        if self._closed:
            raise RuntimeError("WindowHost is closed")
        current = {key: w for key, w in self.widgets.items() if w.app is self.app and not w._closed}
        current.update({str(key): w for key, (w, _, _) in frame.overlays.items() if not w._closed})
        items, styles, style_ids, cells, attachments = [], [], {}, [], []
        for key, widget in current.items():
            if isinstance(widget.buffer, GpuFrameSource):
                raise RuntimeError("Linux WindowHost currently supports native CPU shared frames; GPU import unavailable")
            info = widget.buffer.info
            placement = frame.overlays.get(id(widget))
            rect = placement[1] if placement else widget.rect
            clip = placement[2] if placement else rect.inset(max(rect.width, rect.height))
            visible = bool(placement and clip.width and clip.height)
            item = dict(id=key, token=widget._event_token, source=asdict(info),
                rect=list(asdict(rect).values()), clip=list(asdict(clip).values()), visible=visible,
                fit=widget.fit, smooth=widget.smooth, input_mode=widget.input_mode,
                enabled=not widget.disabled, ime=widget.ime)
            items.append(item)
            if visible:
                physical = (rect.width * self.geometry["cell_width"] * self.geometry["dpi_scale"],
                            rect.height * self.geometry["cell_height"] * self.geometry["dpi_scale"])
                transform = CanvasTransform.calculate((info.width, info.height), physical,
                                                      widget.fit, self.geometry["dpi_scale"])
                if transform != widget.transform:
                    widget._emit(CanvasEvent("transform", {"transform": transform}))
            if getattr(widget, "_window_source", None) != info:
                attachments.append(widget)
        for index, cell in enumerate(frame.cells):
            if cell.width == 0:
                continue
            style_id = style_ids.get(cell.style)
            if style_id is None:
                style_id = len(styles)
                style_ids[cell.style] = style_id
                styles.append(asdict(cell.style))
            cells.append([index % frame.width, index // frame.width, cell.width, cell.text, style_id])
        state = dict(items=items, styles=styles, cells=cells,
                     focus=str(id(self.app.focused)) if self.app.focused and
                     getattr(self.app.focused, "native_focus", False) else "")
        if state == self._last:
            return
        self._rpc.request("scene", **state)
        self._last = state
        retired = {key: w for key, w in self.widgets.items() if key not in current}
        self.widgets = current
        for widget in attachments:
            widget._window_source = widget.buffer.info
            widget._session = self
            widget._emit(CanvasEvent("buffer_attached", {"buffer": widget.buffer.info}))
        for widget in retired.values():
            widget._session = None
            widget._emit(CanvasEvent("detached"))
        for item in items:
            widget = current[item["id"]]
            if getattr(widget, "_window_visible", None) != item["visible"]:
                widget._window_visible = item["visible"]
                widget._emit(CanvasEvent("show" if item["visible"] else "hide"))

    def step(self, dt=1 / 60):
        observer = self.app.on_frame
        start = time.perf_counter() if observer else 0
        self.poll()
        polled = time.perf_counter() if observer else 0
        frame = self.app.render(self.geometry["columns"], self.geometry["rows"], dt=dt)
        rendered = time.perf_counter() if observer else 0
        self.sync(frame)
        if observer:
            from .renderer import FrameTiming
            interval = start - getattr(self, "_observed_previous", start)
            self._observed_previous = start
            observer(FrameTiming(start, interval, self.app.fps, True, tick=polled - start,
                render=rendered - polled, overlay=time.perf_counter() - rendered, host="window"))
        return frame

    def metrics(self):
        return self._rpc.request("metrics")

    def run(self):
        self.app._running = True
        previous = time.perf_counter()
        pacer = FramePacer(previous)
        try:
            while self.app._running:
                start = time.perf_counter()
                self.step(min(.25, start - previous))
                previous = start
                deadline = pacer.advance(time.perf_counter(), self.app.fps)
                time.sleep(max(0, deadline - time.perf_counter()))
        finally:
            self.app._running = False
            self.close()

    def close(self):
        if self._closed:
            return
        try:
            self._rpc.close()
        finally:
            self._closed = True
            for widget in self.widgets.values():
                widget._session = None
                widget._emit(CanvasEvent("detached"))
            self.widgets.clear()
            self._log_dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
