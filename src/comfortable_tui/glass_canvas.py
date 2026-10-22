"""Host existing canvases in cards without changing their frame transport."""
from __future__ import annotations

import math
import time

from .glass import GlassControl
from .gpu import GpuFrameSource
from .overlay import CanvasEvent, CanvasTransform, FrameBuffer, OverlayCanvas

_KEY_NAMES = {8: "backspace", 9: "tab", 13: "enter", 27: "escape", 32: "space", 33: "pageup",
              34: "pagedown", 35: "end", 36: "home", 37: "left", 38: "up", 39: "right",
              40: "down", 45: "insert", 46: "delete"}
_KEY_NAMES.update({111 + n: f"f{n}" for n in range(1, 25)})


class GlassCanvas(GlassControl):
    """Host OverlayCanvas (including WebView), FrameBuffer or GpuFrameSource.

    CanvasEvent callbacks, buffer replacement and source ownership stay with the
    canvas. A canvas has one host at a time; this adapter never closes its source.
    auto_resize requests the canvas's own resize policy after native attach ack.
    Ordinary image producers choose dimensions in response to transform events.
    """
    _managed = True

    def __init__(self, id, canvas, *, label="", height=240, enabled=True, fit=None,
                 auto_resize=False, on_event=None, effects=(), motion=None, visible=True, weight=1., corner_radius=0.):
        if isinstance(canvas, (FrameBuffer, GpuFrameSource)):
            canvas = OverlayCanvas(canvas, fit=fit or "contain")
        if not isinstance(canvas, OverlayCanvas):
            raise TypeError("GlassCanvas needs an OverlayCanvas, FrameBuffer or GpuFrameSource")
        if not canvas._surface_available:
            raise RuntimeError("Canvas is closed")
        if type(auto_resize) is not bool:
            raise TypeError("auto_resize must be a bool")
        canvas._prepare_surface()
        super().__init__(id, "canvas", label, source=canvas.buffer, enabled=enabled,
                         fit=canvas.fit if fit is None else fit, on_event=on_event,
                         canvas_height=height, capture_escape=True, effects=list(effects), motion=motion,
                         visible=visible, weight=weight, corner_radius=corner_radius)
        self.canvas, self.auto_resize = canvas, auto_resize
        self._inherit_fit = fit is None
        self._workspace = None
        self._submitted = self._acknowledged_source = self._pending_resize = None
        self._display_visible, self._has_focus = False, False
        self._buttons, self._point = set(), (0., 0.)
        self._last_tick = time.perf_counter()
        self._ancestors_visible = self._ancestors_enabled = True

    @property
    def _token(self):
        return self.canvas._event_token

    def snapshot(self):
        self.canvas._prepare_surface()
        self.source = self.canvas.buffer
        if self._inherit_fit:
            self.fit = self.canvas.fit
        result = super().snapshot()
        result["enabled"] = self.enabled and not self.canvas.disabled and self.canvas._surface_available
        return result

    def _canvas_invalidated(self):
        self.source = self.canvas.buffer
        if self._workspace is not None and not self._workspace._closed:
            self._workspace.update()

    def _mount(self, workspace):
        if self._workspace is not None:
            raise RuntimeError("A GlassCanvas may belong to one live workspace")
        self.canvas._mount_surface(self, workspace._app or workspace)
        self._workspace = workspace
        self.source = self.canvas.buffer
        self._last_tick = time.perf_counter()

    def _unmount(self, workspace):
        self._lose_focus()
        self._display_visible = False
        self.canvas._emit(CanvasEvent("hide"))
        self.canvas._emit(CanvasEvent("detached"))
        self.canvas._unmount_surface()
        self._workspace = None
        self._submitted = self._acknowledged_source = None

    def _attaching(self, info):
        if info != self._submitted:
            self._submitted = info
            self.canvas._emit(CanvasEvent("attaching", {"buffer": info}))

    def _attached(self, info):
        if info == self.canvas.buffer.info:
            self._acknowledged_source = info
            self.canvas._emit(CanvasEvent("buffer_attached", {"buffer": info}))

    def _view_visible(self):
        return self._workspace is not None and self._display_visible and self.canvas._surface_available

    def _poll(self, workspace, window):
        if not self.canvas._surface_available and self.enabled:
            self.enabled = False
            workspace.update()
        visible = bool(self.visible and self._ancestors_visible and window and window.get("visible") and id(self) in workspace._desired_controls
                       and workspace.visible and workspace._pane_visible and self.canvas._surface_available)
        if visible != self._display_visible:
            self._display_visible = visible
            self.canvas._emit(CanvasEvent("show" if visible else "hide"))
        if (not visible or not self.enabled or not self._ancestors_enabled or self.canvas.disabled or not window.get("enabled", True)
                or not workspace._metrics.get("active")):
            self._lose_focus()
        now = time.perf_counter()
        self.canvas.tick(max(0., now - self._last_tick))
        self._last_tick = now
        if self._pending_resize and self._acknowledged_source == self.canvas.buffer.info:
            width, height = self._pending_resize
            self._pending_resize = None
            if self.canvas._surface_available and (width, height) != (self.source.info.width, self.source.info.height):
                self.canvas._resize_surface(width, height)

    def _lose_focus(self):
        for button in tuple(self._buttons):
            self.canvas._emit(CanvasEvent("mouse_up", dict(source_position=self._point,
                button={"left": 1, "right": 2, "middle": 4}[button], buttons=0, native=True)))
        self._buttons.clear()
        if self._has_focus or getattr(self.canvas, "interacting", False):
            self.canvas._emit(CanvasEvent("focus_out"))
        self._has_focus = False

    def default_event(self, event, workspace):
        kind = event.kind
        if kind in ("control_focus_out", "mouse_cancel"):
            self._lose_focus()
            return
        if event.source_name and event.source_name != self.canvas.buffer.info.name:
            return
        if kind == "transform":
            dpi = event.value or 1.
            transform = CanvasTransform.calculate((self.source.info.width, self.source.info.height),
                tuple(v * dpi for v in event.position), self.fit, dpi)
            self.canvas._emit(CanvasEvent("transform", {"transform": transform}))
            if self.auto_resize:
                self._pending_resize = tuple(max(1, math.floor(v + .5)) for v in event.position)
            return
        if not self.enabled or not self.visible or not self._ancestors_visible or not self._ancestors_enabled or self.canvas.disabled or not self.canvas._surface_available:
            return
        data = dict(native=True, modifiers=event.modifiers)
        if kind == "control_focus_in":
            self._has_focus = True
            self.canvas._emit(CanvasEvent("focus_in", data))
            return
        if kind in ("key_down", "key_up"):
            code = int(event.text)
            name = _KEY_NAMES.get(code, chr(code).lower() if 32 <= code <= 126 else "")
            data.update(name="+".join((*event.modifiers, name)), virtual_key=code,
                        scan_code=event.scan_code, repeat=event.repeat,
                        text="\r" if code == 13 and kind == "key_down" else "")
        elif kind == "text":
            data["commit"] = event.text
        elif kind in ("mouse_down", "mouse_move", "mouse_up", "mouse_leave", "wheel", "wheel_horizontal"):
            self._point = event.position
            if kind == "mouse_down":
                if not event.inside_source:
                    return
                self._buttons.add(event.button)
            elif kind == "mouse_up":
                self._buttons.discard(event.button)
            if kind == "mouse_move" and not event.inside_source and not self._buttons:
                kind = "mouse_leave"
            data.update(source_position=event.position, inside_source=event.inside_source,
                        button={"left": 1, "right": 2, "middle": 4}[event.button],
                        buttons=event.buttons, clicks=event.clicks)
            if kind == "mouse_down" and event.clicks == 2:
                kind = "mouse_double_click"
            if kind in ("wheel", "wheel_horizontal"):
                data["angle_delta"] = (event.value * 120, 0) if kind == "wheel_horizontal" else (0, event.value * 120)
                kind = "wheel"
        else:
            return
        consumed = self.canvas._emit(CanvasEvent(kind, data))
        if not consumed and kind == "key_down":
            if code in (37, 38, 39, 40):
                workspace.command("previous" if code in (37, 38) else "next")
            elif code == 27:
                workspace.command("focus_terminal")
