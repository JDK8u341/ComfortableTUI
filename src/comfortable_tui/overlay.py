"""Buffer-backed native overlay canvas. Importing this module does not import Qt."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import math
import multiprocessing as mp
import os
import queue
import time
import uuid

from ._shared_frame import FrameBuffer, FrameBufferInfo, FrameLease
from .events import Key, Mouse, Paste
from .gpu import GpuFrameSource
from .widget import Widget

__all__ = ["FrameBuffer", "FrameBufferInfo", "FrameLease", "PixelRect", "CanvasTransform",
           "CanvasEvent", "TerminalHost", "OverlayCanvas"]


@dataclass(frozen=True)
class PixelRect:
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.x, self.y, self.width, self.height)):
            raise ValueError("pixel coordinates must be finite")
        if min(self.width, self.height) < 0:
            raise ValueError("pixel dimensions must be nonnegative")


@dataclass(frozen=True)
class CanvasTransform:
    """All lengths in physical pixels; source coordinates refer to frame pixels."""

    source_size: tuple[int, int]
    viewport_size: tuple[float, float]
    destination: PixelRect
    dpi_scale: float = 1.0

    @classmethod
    def calculate(cls, source_size, viewport_size, fit="contain", dpi_scale=1.0):
        sw, sh = source_size
        vw, vh = viewport_size
        if min(sw, sh) <= 0 or min(vw, vh) < 0 or not all(
                math.isfinite(v) for v in (*source_size, *viewport_size, dpi_scale)) or dpi_scale <= 0:
            raise ValueError("invalid source size, viewport size or DPI scale")
        if fit == "stretch":
            width, height = vw, vh
        elif fit in ("contain", "cover", "pixel"):
            scale = 1.0 if fit == "pixel" else (min if fit == "contain" else max)(vw / sw, vh / sh)
            width, height = sw * scale, sh * scale
        else:
            raise ValueError("fit must be contain, cover, stretch or pixel")
        return cls(tuple(source_size), tuple(viewport_size),
                   PixelRect((vw - width) / 2, (vh - height) / 2, width, height), dpi_scale)

    @property
    def scale(self):
        return (self.destination.width / self.source_size[0],
                self.destination.height / self.source_size[1])

    def source_at(self, x, y):
        sx, sy = self.scale
        if not sx or not sy:
            return None
        return ((x - self.destination.x) / sx, (y - self.destination.y) / sy)

    def contains_source(self, x, y):
        position = self.source_at(x, y)
        return position is not None and 0 <= position[0] < self.source_size[0] and 0 <= position[1] < self.source_size[1]


@dataclass(frozen=True)
class CanvasEvent:
    kind: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.perf_counter)


@dataclass(frozen=True)
class TerminalHost:
    """Optional HWND and client-relative physical-pixel text viewport.

    Without viewport, UI Automation measures and pins a terminal text element.
    pane_index disambiguates several text providers when none is focused.
    """

    hwnd: int | None = None
    viewport: PixelRect | None = None
    pane_index: int | None = None
    follow_resize: bool = True
    _runtime_id: tuple[int, ...] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.hwnd is not None and self.hwnd <= 0:
            raise ValueError("hwnd must be a positive window handle")
        if self.viewport is not None and min(self.viewport.width, self.viewport.height) <= 0:
            raise ValueError("viewport must have a positive size")
        if self.pane_index is not None and self.pane_index < 0:
            raise ValueError("pane_index must be nonnegative")


class OverlayCanvas(Widget):
    """A logical canvas composited into the App's shared transparent layer.

    on_canvas_event is always called on the TUI thread. Return True to consume
    navigation keys. App bindings (Tab/F6/Ctrl+Q etc.) retain their meaning.
    Frame producers need no Qt APIs and may run in another thread or process.
    """

    focusable = True
    focus_indicator = False

    def __init__(self, buffer: FrameBuffer | GpuFrameSource, *, on_event=None, fit="contain", smooth=True,
                 input_mode="capture", host: TerminalHost | None = None, fps=120, **kwargs):
        super().__init__(**kwargs)
        self._validate(fit, input_mode, fps)
        buffer._check_open()
        self.buffer, self.on_canvas_event = buffer, on_event
        self.fit, self.smooth, self.input_mode = fit, bool(smooth), input_mode
        self.host, self.fps = host or TerminalHost(), fps
        self.transform: CanvasTransform | None = None
        self.ime = {"cursor": (0, 0, 1, 20), "text": "", "position": 0, "anchor": 0}
        self._session = None
        self._closed = False
        self._detached = False
        self._event_token = uuid.uuid4().hex

    @staticmethod
    def _validate(fit, input_mode, fps):
        if fit not in ("contain", "cover", "stretch", "pixel"):
            raise ValueError("fit must be contain, cover, stretch or pixel")
        if input_mode not in ("capture", "passthrough"):
            raise ValueError("input_mode must be capture or passthrough")
        if not isinstance(fps, int) or not 1 <= fps <= 240:
            raise ValueError("fps must be an integer between 1 and 240")

    def configure(self, *, fit=None, smooth=None, input_mode=None):
        fit = self.fit if fit is None else fit
        mode = self.input_mode if input_mode is None else input_mode
        self._validate(fit, mode, self.fps)
        self.fit, self.input_mode = fit, mode
        if smooth is not None:
            self.smooth = bool(smooth)
        self.invalidate()

    def set_buffer(self, buffer: FrameBuffer | GpuFrameSource):
        """Keep the old buffer alive until the new buffer_attached event arrives."""
        buffer._check_open()
        self.buffer = buffer
        self.invalidate()

    def set_ime(self, *, cursor=(0, 0, 1, 20), text="", position=0, anchor=None):
        """IME caret is in source pixels; surrounding text positions are UTF-16 offsets."""
        PixelRect(*cursor)
        if position < 0 or anchor is not None and anchor < 0:
            raise ValueError("IME positions must be nonnegative")
        self.ime = {"cursor": tuple(cursor), "text": str(text), "position": position,
                    "anchor": position if anchor is None else anchor}
        self.invalidate()

    def paint(self, canvas):
        if not self._closed:
            canvas.overlays[id(self)] = (self, self.rect, self.rect.intersect(canvas.clip))

    def _emit(self, event):
        if event.kind == "detached":
            if self._detached:
                return False
            self._detached = True
        if event.kind == "transform":
            self.transform = event.data["transform"]
        elif event.kind in ("focus_in", "mouse_down") and self.app:
            ancestor, child = self.parent, self
            while ancestor:
                rows = getattr(ancestor, "_row_widgets", {})
                index = next((i for i, row in rows.items() if row is child), None)
                if index is not None:
                    ancestor.select(index)
                    break
                child, ancestor = ancestor, ancestor.parent
            self.app.focus(self)
        consumed = bool(self.on_canvas_event(event)) if self.on_canvas_event else False
        return consumed

    def _native_event(self, event):
        consumed = self._emit(event)
        if self.app and event.kind == "wheel" and not consumed:
            x, y = event.data.get("terminal_position", (self.rect.x, self.rect.y))
            delta = event.data.get("angle_delta", (0, 0))[1] or event.data.get("pixel_delta", (0, 0))[1]
            parent = self.parent
            while parent:
                if not parent.disabled and parent.on_event(Mouse(x, y, 64 if delta > 0 else 65, "scroll")):
                    self.app.invalidate()
                    break
                parent = parent.parent
        if self.app and event.kind in ("key_down", "key_up"):
            name = event.data.get("name", "")
            phase = "release" if event.kind == "key_up" else "repeat" if event.data.get("repeat") else "press"
            if (name in self.app.bindings or name in ("tab", "shift+tab", "f6") or
                    self.app.modal is not None and name == "escape"):
                self.app.dispatch(Key(name, text=event.data.get("text", ""), phase=phase))
            elif not consumed:
                parent = self.parent
                key = Key(name, text=event.data.get("text", ""), phase=phase)
                while parent:
                    if not parent.disabled and parent.on_event(key):
                        self.app.invalidate()
                        break
                    parent = parent.parent
                else:
                    if phase != "release" and name in ("left", "right", "up", "down"):
                        self.app.focus_direction(name)

    def on_event(self, event):
        if isinstance(event, Key):
            return self._emit(CanvasEvent("key_up" if event.phase == "release" else "key_down",
                                         {"name": event.name, "text": event.text,
                                          "repeat": event.phase == "repeat", "native": False}))
        if isinstance(event, Paste):
            return self._emit(CanvasEvent("text", {"commit": event.text, "native": False}))
        if isinstance(event, Mouse):
            # Native input provides precise source coordinates. This path handles
            # terminal input before the helper is visible or in passthrough mode.
            return self._emit(CanvasEvent("terminal_mouse", {"event": event, "native": False}))
        return False

    def close(self):
        if self._closed:
            return
        session = self._session
        self._session = None
        self._closed = True
        if session is None:
            self._emit(CanvasEvent("detached"))
        self.invalidate()

    def unmount(self):
        self.close()
        super().unmount()


class _OverlaySession:
    """One GUI process/window per App terminal pane; items remain logical widgets."""

    def __init__(self, widget):
        if os.name != "nt":
            raise RuntimeError("OverlayCanvas currently requires a local Windows desktop")
        if importlib.util.find_spec("PySide6") is None:
            raise RuntimeError('OverlayCanvas requires: pip install "comfortable-tui[overlay]"')
        from ._win32_host import resolve_host
        from ._overlay_qt import run_overlay
        self.widget = widget
        self.app = widget.app
        self.widgets = {}
        self.retired = {}
        self.host_spec = widget.host
        self._last = None
        self._pending = None
        self._failed = False
        self._closed = False
        app = widget.app
        cached = getattr(app, "_native_overlay_host", None)
        if widget.host == TerminalHost() and cached is not None:
            host = cached
        else:
            try:
                host = resolve_host(widget.host, terminal=app._terminal if app else None,
                                    style=widget.theme.background)
            finally:
                if app:
                    app.renderer.invalidate()
                    app.invalidate()  # Erase the temporary binding marker outside diff state.
            if app and widget.host == TerminalHost():
                app._native_overlay_host = host
        context = mp.get_context("spawn")
        self._commands = context.Queue(maxsize=1)
        self._events = context.Queue(maxsize=512)
        self._stop = context.Event()
        self._process = context.Process(target=run_overlay,
                                        args=(host, self._commands, self._events,
                                              self._stop, os.getpid()), daemon=True)
        self._process.start()
        self._started = time.perf_counter()

    def sync(self, placements, grid):
        for key, widget in self.widgets.items():
            if widget.app is not self.app or widget._closed:
                self.retired[widget._event_token] = (key, widget)
        self.widgets = {key: widget for key, widget in self.widgets.items()
                        if widget.app is self.app and not widget._closed}
        for key, (widget, _, _) in placements.items():
            if not widget._closed:
                if widget.host not in (TerminalHost(), self.host_spec):
                    raise ValueError("all OverlayCanvas widgets in an App must use the same TerminalHost")
                self.widgets[key] = widget
                widget._session = self
        items = []
        # Placement order is paint order. Hidden items follow, never hit-testable.
        for key in dict.fromkeys((*placements, *self.widgets)):
            if key not in self.widgets:
                continue
            widget = self.widgets[key]
            placement = placements.get(key)
            items.append({"id": key, "token": widget._event_token,
                          "placement": (placement[1], placement[2]) if placement else None,
                          "buffer": widget.buffer.info, "fit": widget.fit, "smooth": widget.smooth,
                          "input_mode": widget.input_mode, "enabled": not widget.disabled,
                          "focused": widget.focused, "ime": widget.ime, "fps": widget.fps})
        state = {"grid": grid, "items": items,
                 "retired": [{"id": key, "token": token} for token, (key, _) in self.retired.items()]}
        self._pending = state if state != self._last else None
        self._flush_state()

    def _flush_state(self):
        if not self._failed and self._pending is not None:
            try:
                self._commands.put_nowait(self._pending)
                self._last, self._pending = self._pending, None
            except queue.Full:
                pass  # Retain only the most recent desired state; retry even on idle ticks.
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._failed = True
                raise RuntimeError("OverlayCanvas helper connection closed") from exc

    def poll(self):
        if self._closed:
            return
        self._flush_state()
        for _ in range(512):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            token = event.data.get("canvas_token")
            if event.kind == "detached" and token in self.retired:
                _, retired = self.retired.pop(token)
                retired._emit(event)
                self.app.invalidate()
                continue
            key = event.data.get("canvas_id")
            widget = self.widgets.get(key)
            if widget and event.data.get("canvas_token", widget._event_token) != widget._event_token:
                continue
            if event.kind == "tui_mouse":
                self.app.dispatch(Mouse(**event.data["event"]))
            elif widget and not widget._closed and widget.app is self.app:
                # Ignore late input for a page/row which has already disappeared.
                if event.kind not in ("mouse_down", "mouse_up", "mouse_move", "key_down", "key_up", "focus_in") or key in self.app._overlay_placements:
                    widget._native_event(event)
            elif key is None:
                for widget in tuple(self.widgets.values()):
                    widget._emit(event)
            if self._closed:
                return
            if event.kind == "error" and event.data.get("fatal"):
                self._failed = True
                raise RuntimeError("OverlayCanvas: " + event.data["message"])
        if not self._failed and not self._process.is_alive():
            self._failed = True
            raise RuntimeError(f"OverlayCanvas helper exited unexpectedly ({self._process.exitcode})")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._commands.cancel_join_thread()
        self._commands.close()
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._events.close()
        self._events.join_thread()
        for widget in (*self.widgets.values(), *(widget for _, widget in self.retired.values())):
            widget._emit(CanvasEvent("detached", {"canvas_id": id(widget), "canvas_token": widget._event_token}))
        self.retired.clear()
