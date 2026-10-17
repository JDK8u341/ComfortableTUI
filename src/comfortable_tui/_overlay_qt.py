"""Qt helper implementation; Qt is imported only inside the helper or GUI tests."""
from __future__ import annotations

from collections import deque
import os
import queue
import time
import traceback

from ._shared_frame import FrameBuffer
from .overlay import CanvasEvent, CanvasTransform, PixelRect


class _EventSink:
    """Bounded input backlog. High-rate state events are coalesced, never pixels."""
    _coalesced = {"mouse_move", "transform", "move", "resize", "metrics", "host_status", "layer_status", "detached"}

    def __init__(self, output):
        self.output, self.pending, self.latest = output, deque(), {}
        self.lost = 0

    def emit(self, event):
        if event.kind in self._coalesced:
            self.latest[(event.data.get("canvas_id"), event.data.get("canvas_token"), event.kind)] = event
        else:
            if len(self.pending) >= 1024:
                self.pending.popleft()
                self.lost += 1
            self.pending.append(event)

    def flush(self):
        if self.lost:
            self.latest["events_dropped"] = CanvasEvent("events_dropped", {"count": self.lost})
            self.lost = 0
        while self.pending:
            try:
                self.output.put_nowait(self.pending[0])
            except queue.Full:
                return
            self.pending.popleft()
        for kind in list(self.latest):
            try:
                self.output.put_nowait(self.latest[kind])
            except queue.Full:
                return
            del self.latest[kind]


def _make_window_class():
    from PySide6.QtCore import QEvent, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QWindow
    from PySide6.QtWidgets import QApplication, QWidget

    formats = {"rgba8": QImage.Format.Format_RGBA8888, "bgra8": QImage.Format.Format_ARGB32,
               "bgra8_premultiplied": QImage.Format.Format_ARGB32_Premultiplied,
               "rgb8": QImage.Format.Format_RGB888}

    def modifiers(value):
        return tuple(name for flag, name in ((Qt.KeyboardModifier.ControlModifier, "ctrl"),
                                            (Qt.KeyboardModifier.AltModifier, "alt"),
                                            (Qt.KeyboardModifier.ShiftModifier, "shift"),
                                            (Qt.KeyboardModifier.MetaModifier, "meta")) if value & flag)

    def key_name(event):
        special = {Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Escape: "escape",
                   Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backtab: "tab", Qt.Key.Key_Backspace: "backspace",
                   Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert", Qt.Key.Key_Home: "home",
                   Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
                   Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up",
                   Qt.Key.Key_Down: "down", Qt.Key.Key_Space: "space"}
        code = event.key()
        if code in special:
            name = special[code]
        elif Qt.Key.Key_F1 <= code <= Qt.Key.Key_F35:
            name = f"f{code - Qt.Key.Key_F1 + 1}"
        elif 32 <= code <= 0x10ffff:
            name = chr(code).lower()
        else:
            name = f"qt:{code}"
        mods = list(modifiers(event.modifiers()))
        if code == Qt.Key.Key_Backtab and "shift" not in mods:
            mods.append("shift")
        return "+".join((*mods, name))

    class OverlayWindow(QWidget):
        def __init__(self, buffer, emit, owner=None, parent=None):
            # Child surfaces are alien widgets: never call winId() on them.
            super().__init__(parent, Qt.WindowType.Widget if parent else
                             Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            self.buffer, self.emit, self.owner = buffer, emit, owner
            self.api = None
            self.full, self.clipped = PixelRect(), PixelRect()
            self.transform = None
            self.fit, self.smooth, self.input_mode = "contain", True, "capture"
            self.ime = {"cursor": (0, 0, 1, 20), "text": "", "position": 0, "anchor": 0}
            self._logical_focus = False
            self._want_focus = False
            self._pressed_buttons = 0
            self._sequence = 0
            self._frames = self._skipped = 0
            self._draw_ms = self._age_ms = 0.0
            self._metrics_time = time.perf_counter()
            self.setWindowTitle("ComfortableTUI OverlayCanvas")
            if parent and getattr(parent, "native_renderer", False):
                self.setUpdatesEnabled(False)
            else:
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
            self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
            self.setMouseTracking(True)
            self.setTabletTracking(True)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setAcceptDrops(True)
            if owner:
                from ._win32_host import Win32
                self.api = Win32()
                self._foreign_owner = QWindow.fromWinId(owner)
                self.winId()  # Create native HWND before assigning the Qt relationship.
                self.windowHandle().setTransientParent(self._foreign_owner)
                self.api.own(int(self.winId()), owner)

        def configure(self, state):
            changed = self.fit != state["fit"] or self.smooth != state["smooth"]
            self.fit, self.smooth = state["fit"], state["smooth"]
            mode = state["input_mode"]
            if mode != self.input_mode:
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, mode == "passthrough")
                if self.api and self.isWindow():
                    hwnd = int(self.winId())
                    # Qt's widget flag alone does not route input to another process.
                    get_style = self.api.user.GetWindowLongPtrW
                    import ctypes
                    get_style.argtypes, get_style.restype = [ctypes.c_void_p, ctypes.c_int], ctypes.c_ssize_t
                    style = get_style(hwnd, -20)
                    self.api.user.SetWindowLongPtrW(hwnd, -20, (style | 0x20) if mode == "passthrough"
                                                   else (style & ~0x20))  # WS_EX_TRANSPARENT
                self.input_mode = mode
                changed = True
            self.setEnabled(state["enabled"])
            self.setAcceptDrops(mode == "capture" and state["enabled"])
            self.ime = state["ime"]
            self._want_focus = (state["focused"] and mode == "capture" and
                                (self._want_focus or not self._logical_focus))
            self._logical_focus = state["focused"]
            layer = self.window()
            api = self.api or getattr(layer, "api", None)
            if (not self._logical_focus or mode == "passthrough") and self.hasFocus() and api:
                api.user.SetForegroundWindow(layer.owner)
                self.clearFocus()
            QApplication.inputMethod().update(Qt.InputMethodQuery.ImQueryAll)
            if changed:
                self._transform()
                self.update()

        def _transform(self):
            transform = CanvasTransform.calculate((self.buffer.info.width, self.buffer.info.height),
                                                   (self.full.width, self.full.height), self.fit,
                                                   self.devicePixelRatioF())
            if transform != self.transform or getattr(self, "_transform_geometry", None) != (self.full, self.clipped):
                previous = self.transform
                self.transform = transform
                self._transform_geometry = (self.full, self.clipped)
                self.emit(CanvasEvent("transform", {"transform": transform, "previous": previous,
                                                     "screen_rect": self.full, "clip_rect": self.clipped}))

        def place(self, full, clipped):
            changed = full != self.full or clipped != self.clipped
            self.full, self.clipped = full, clipped
            x, y = round(clipped.x), round(clipped.y)
            width = max(1, round(clipped.x + clipped.width) - x)
            height = max(1, round(clipped.y + clipped.height) - y)
            if self.parentWidget():
                layer = self.parentWidget()
                dpr = layer.devicePixelRatioF()
                self.setGeometry(round((x - layer.clipped.x) / dpr), round((y - layer.clipped.y) / dpr),
                                 max(1, round(width / dpr)), max(1, round(height / dpr)))
            elif self.api:
                # Qt owns its backing-store geometry. Mixing raw SetWindowPos
                # sizes with Qt's logical sizes makes layered-window flushes
                # reset the HWND at fractional DPI. Convert explicitly instead.
                device, native_x, native_y = self.api.monitor(clipped)
                screen = next((s for s in QApplication.screens() if s.name() == device), self.screen())
                if self.windowHandle().screen() != screen:
                    self.windowHandle().setScreen(screen)
                dpr = screen.devicePixelRatio()
                logical_x = screen.geometry().x() + (x - native_x) / dpr
                logical_y = screen.geometry().y() + (y - native_y) / dpr
                self.setGeometry(round(logical_x), round(logical_y), max(1, round(width / dpr)),
                                 max(1, round(height / dpr)))
            else:
                if changed:
                    self.setGeometry(x, y, width, height)
            self._transform()
            if changed:
                self.update()

        def request_focus(self):
            if self._want_focus and self.input_mode == "capture" and self.isVisible() and self.isEnabled():
                self.window().activateWindow()
                self.setFocus(Qt.FocusReason.OtherFocusReason)
                self._want_focus = False

        def paintEvent(self, event):
            if self.parentWidget() and getattr(self.parentWidget(), "native_renderer", False):
                return
            started = time.perf_counter()
            painter = QPainter(self)
            try:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                # Alpha=1 retains hit testing on Windows layered windows. With
                # passthrough mode genuinely transparent pixels remain alpha=0.
                painter.fillRect(self.rect(), QColor(0, 0, 0, 1 if self.input_mode == "capture" else 0))
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                if not self.transform:
                    self._transform()
                with self.buffer.read() as frame:
                    if frame is not None:
                        info = self.buffer.info
                        image = QImage(frame.pixels, info.width, info.height, info.stride, formats[info.pixel_format])
                        try:
                            rect, dpr = self.transform.destination, self.devicePixelRatioF()
                            target = QRectF((rect.x + self.full.x - self.clipped.x) / dpr,
                                            (rect.y + self.full.y - self.clipped.y) / dpr,
                                            rect.width / dpr, rect.height / dpr)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
                            painter.drawImage(target, image)
                        finally:
                            # Qt must release its buffer reference before ending the lease.
                            del image
                        if frame.sequence != self._sequence:
                            self._skipped += max(0, frame.sequence - self._sequence - 1)
                            self._frames += 1
                            self._sequence = frame.sequence
                        self._age_ms = (time.perf_counter() - frame.timestamp) * 1000
            finally:
                painter.end()
            self._draw_ms = (time.perf_counter() - started) * 1000

        def report_metrics(self):
            now = time.perf_counter()
            if now - self._metrics_time >= 1:
                self.emit(CanvasEvent("metrics", {"painted_fps": self._frames / (now - self._metrics_time),
                                                 "skipped_frames": self._skipped, "sequence": self._sequence,
                                                 "draw_ms": self._draw_ms, "frame_age_ms": self._age_ms}))
                self._frames = self._skipped = 0
                self._metrics_time = now

        def _position(self, position):
            dpr = self.devicePixelRatioF()
            x = position.x() * dpr + self.clipped.x - self.full.x
            y = position.y() * dpr + self.clipped.y - self.full.y
            data = {"position": (x, y), "source_position": self.transform.source_at(x, y) if self.transform else None,
                    "inside_source": self.transform.contains_source(x, y) if self.transform else False,
                    "screen_position": (x + self.full.x, y + self.full.y)}
            if self.parentWidget():
                layer = self.parentWidget()
                data["terminal_position"] = (
                    int((x + self.full.x - layer.clipped.x) * layer.grid[0] / max(1, layer.clipped.width)),
                    int((y + self.full.y - layer.clipped.y) * layer.grid[1] / max(1, layer.clipped.height)))
            return data

        def _mouse(self, kind, event):
            self._pressed_buttons = event.buttons().value
            data = self._position(event.position())
            data.update(button=event.button().value, buttons=event.buttons().value,
                        modifiers=modifiers(event.modifiers()), native=True)
            self.emit(CanvasEvent(kind, data))
            event.accept()

        def mousePressEvent(self, event):
            self.window().activateWindow()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._mouse("mouse_down", event)

        def mouseReleaseEvent(self, event):
            self._mouse("mouse_up", event)

        def mouseMoveEvent(self, event):
            self._mouse("mouse_move", event)

        def mouseDoubleClickEvent(self, event):
            self._mouse("mouse_double_click", event)

        def wheelEvent(self, event):
            data = self._position(event.position())
            data.update(angle_delta=(event.angleDelta().x(), event.angleDelta().y()),
                        pixel_delta=(event.pixelDelta().x(), event.pixelDelta().y()),
                        modifiers=modifiers(event.modifiers()), inverted=event.inverted(),
                        phase=event.phase().name)
            self.emit(CanvasEvent("wheel", data))
            event.accept()

        def enterEvent(self, event):
            self.emit(CanvasEvent("mouse_enter", self._position(event.position())))

        def leaveEvent(self, event):
            self.emit(CanvasEvent("mouse_leave"))

        def keyPressEvent(self, event):
            self._key("key_down", event)

        def keyReleaseEvent(self, event):
            self._key("key_up", event)

        def _key(self, kind, event):
            self.emit(CanvasEvent(kind, {"name": key_name(event), "text": event.text(),
                                        "key": event.key(), "scan_code": event.nativeScanCode(),
                                        "virtual_key": event.nativeVirtualKey(), "repeat": event.isAutoRepeat(),
                                        "modifiers": modifiers(event.modifiers()), "native": True}))
            event.accept()

        def focusInEvent(self, event):
            self.emit(CanvasEvent("focus_in", {"reason": event.reason().name}))

        def focusOutEvent(self, event):
            self.emit(CanvasEvent("focus_out", {"reason": event.reason().name}))

        def showEvent(self, event):
            self._want_focus |= self._logical_focus and self.input_mode == "capture"
            self.emit(CanvasEvent("show"))

        def hideEvent(self, event):
            if self._pressed_buttons:
                self.emit(CanvasEvent("pointer_cancel", {"buttons": self._pressed_buttons}))
                self._pressed_buttons = 0
            self.emit(CanvasEvent("hide"))

        def moveEvent(self, event):
            self.emit(CanvasEvent("move", {"screen_rect": self.clipped}))

        def resizeEvent(self, event):
            self.emit(CanvasEvent("resize", {"logical_size": (event.size().width(), event.size().height()),
                                             "screen_rect": self.clipped}))

        def closeEvent(self, event):
            event.ignore()  # Provider decides whether to close the canvas or the app.
            self.emit(CanvasEvent("close_request"))

        def contextMenuEvent(self, event):
            data = self._position(event.pos())
            data["reason"] = event.reason().name
            self.emit(CanvasEvent("context_menu", data))
            event.accept()

        def inputMethodEvent(self, event):
            attributes = []
            for item in event.attributes():
                value = item.value
                if hasattr(value, "foreground"):
                    value = value.toCharFormat() if hasattr(value, "toCharFormat") else value
                    foreground, background = value.foreground(), value.background()
                    value = {"foreground": foreground.color().name(QColor.NameFormat.HexArgb)
                             if foreground.style() != Qt.BrushStyle.NoBrush else None,
                             "background": background.color().name(QColor.NameFormat.HexArgb)
                             if background.style() != Qt.BrushStyle.NoBrush else None,
                             "underline": value.fontUnderline()}
                elif not isinstance(value, (str, int, float, bool, type(None))):
                    value = str(value)
                attributes.append({"type": item.type.name, "start": item.start, "length": item.length,
                                   "value": value})
            self.emit(CanvasEvent("ime", {"preedit": event.preeditString(), "commit": event.commitString(),
                                         "replacement_start": event.replacementStart(),
                                         "replacement_length": event.replacementLength(),
                                         "attributes": attributes}))
            event.accept()

        def inputMethodQuery(self, query):
            if query == Qt.InputMethodQuery.ImEnabled:
                return self.input_mode == "capture" and self.isEnabled()
            if query == Qt.InputMethodQuery.ImSurroundingText:
                return self.ime["text"]
            if query == Qt.InputMethodQuery.ImCursorPosition:
                return self.ime["position"]
            if query == Qt.InputMethodQuery.ImAnchorPosition:
                return self.ime["anchor"]
            if query == Qt.InputMethodQuery.ImCurrentSelection:
                raw = self.ime["text"].encode("utf-16-le")
                start, end = sorted((self.ime["position"], self.ime["anchor"]))
                return raw[start * 2:end * 2].decode("utf-16-le", "replace")
            if query == Qt.InputMethodQuery.ImCursorRectangle and self.transform:
                x, y, w, h = self.ime["cursor"]
                sx, sy = self.transform.scale
                rect, dpr = self.transform.destination, self.devicePixelRatioF()
                return QRectF((x * sx + rect.x + self.full.x - self.clipped.x) / dpr,
                              (y * sy + rect.y + self.full.y - self.clipped.y) / dpr,
                              w * sx / dpr, h * sy / dpr)
            return super().inputMethodQuery(query)

        def _drag(self, kind, event):
            data = self._position(event.position())
            mime = event.mimeData()
            # Bounded metadata; image and arbitrary MIME bytes never travel as input IPC.
            data.update(text=mime.text()[:65536], urls=[url.toString() for url in mime.urls()[:128]],
                        formats=list(mime.formats())[:64], proposed_action=event.proposedAction().name)
            self.emit(CanvasEvent(kind, data))
            # This API transfers metadata, not files. Never acknowledge a move
            # that a drag source could interpret as permission to delete data.
            if event.possibleActions() & Qt.DropAction.CopyAction:
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
            else:
                event.ignore()

        def dragEnterEvent(self, event):
            self._drag("drag_enter", event)

        def dragMoveEvent(self, event):
            self._drag("drag_move", event)

        def dragLeaveEvent(self, event):
            self.emit(CanvasEvent("drag_leave"))
            event.accept()

        def dropEvent(self, event):
            self._drag("drop", event)

        def tabletEvent(self, event):
            data = self._position(event.position())
            data.update(pressure=event.pressure(), x_tilt=event.xTilt(), y_tilt=event.yTilt(),
                        rotation=event.rotation(), buttons=event.buttons().value, phase=event.type().name)
            self.emit(CanvasEvent("tablet", data))
            event.accept()

        def event(self, event):
            kind = event.type()
            if kind == QEvent.Type.ShortcutOverride:
                event.accept()
                return True
            # QWidget normally intercepts Tab before keyPressEvent.
            if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                self._key("key_down" if kind == QEvent.Type.KeyPress else "key_up", event)
                return True
            if kind in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
                        QEvent.Type.TouchCancel):
                points = [dict(self._position(point.position()), id=point.id(), pressure=point.pressure(),
                               state=point.state().name) for point in event.points()]
                self.emit(CanvasEvent("touch", {"phase": kind.name, "points": points}))
                event.accept()
                return True
            if kind == QEvent.Type.NativeGesture:
                self.emit(CanvasEvent("gesture", dict(self._position(event.position()),
                                                      type=event.gestureType().name, value=event.value())))
                event.accept()
                return True
            if kind == QEvent.Type.WindowStateChange:
                self.emit(CanvasEvent("window_state", {"state": self.windowState().value,
                                                       "old_state": event.oldState().value}))
            elif kind in (QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate):
                self.emit(CanvasEvent("activate" if kind == QEvent.Type.WindowActivate else "deactivate"))
            return super().event(event)

    return OverlayWindow


def _pixel_placement(placement, grid, viewport):
    full, clip = placement
    columns, rows = grid
    if columns <= 0 or rows <= 0:
        return None

    def convert(rect):
        x = viewport.x + rect.x * viewport.width / columns
        y = viewport.y + rect.y * viewport.height / rows
        right = viewport.x + (rect.x + rect.width) * viewport.width / columns
        bottom = viewport.y + (rect.y + rect.height) * viewport.height / rows
        return PixelRect(round(x), round(y), max(0, round(right) - round(x)), max(0, round(bottom) - round(y)))

    return convert(full), convert(clip)


def _make_layer_class():
    """A single native window with non-native QWidget surfaces and one backing store."""
    from PySide6.QtCore import QEvent, QRect, Qt
    from PySide6.QtGui import QColor, QPainter, QRegion, QWindow
    from PySide6.QtWidgets import QApplication, QWidget
    from .gpu import GpuFrameInfo, _FrameReference
    Surface = _make_window_class()

    class OverlayLayer(QWidget):
        def __init__(self, emit, owner=None, native=False):
            super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            self.emit, self.owner, self.api = emit, owner, None
            self.items, self.states, self.scheduled = {}, {}, {}
            self.full, self.clipped = PixelRect(), PixelRect()
            self.grid = (80, 24)
            self.native_renderer = bool(native)
            self.renderer, self.renderer_luid = None, None
            self.renderer_hwnd = None
            self._native_metrics_time, self._native_counts = time.perf_counter(), {}
            self.setWindowTitle("ComfortableTUI shared overlay layer")
            if native:
                self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
                self.setUpdatesEnabled(False)
            else:
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setMouseTracking(True)
            if owner:
                from ._win32_host import Win32
                self.api = Win32()
                self._foreign_owner = QWindow.fromWinId(owner)
                self.winId()
                self.windowHandle().setTransientParent(self._foreign_owner)
                self.api.own(int(self.winId()), owner)

        place = Surface.place

        def _transform(self):
            pass  # The layer has terminal coordinates; each surface has its own transform.

        def apply(self, state, viewport):
            self.grid = state["grid"]
            self.place(viewport, viewport)
            attached = []
            wanted = {item["id"]: item for item in state["items"]}
            for key in list(self.items):
                incoming = wanted.get(key)
                if key not in wanted or self.states[key].get("token") != incoming.get("token"):
                    item = self.items.pop(key)
                    item.hide()
                    item.buffer.close()
                    item.deleteLater()
                    self.states.pop(key, None)
                    self.scheduled.pop(key, None)
            for spec in state["items"]:
                key = spec["id"]
                item = self.items.get(key)
                if item is None:
                    def route(event, key=key, token=spec.get("token")):
                        self.emit(CanvasEvent(event.kind, dict(event.data, canvas_id=key,
                                              canvas_token=token, layer_rect=self.clipped), event.timestamp))
                    if isinstance(spec["buffer"], GpuFrameInfo) and not self.native_renderer:
                        raise RuntimeError("GPU sources require the native GPU extension and Windows Qt platform")
                    reference = _FrameReference(spec["buffer"]) if self.native_renderer else FrameBuffer.attach(spec["buffer"])
                    item = Surface(reference, route, parent=self)
                    self.items[key] = item
                    item.emit(CanvasEvent("ready", {"hwnd": int(self.winId()), "pid": os.getpid(),
                                          "host_hwnd": self.owner, "shared_layer": True,
                                          "platform": QApplication.platformName(),
                                          "renderer": "d3d11" if self.native_renderer else "qt-raster",
                                          "host_class": self.api.class_name(self.owner) if self.api else ""}))
                    attached.append(item)
                if item.buffer.info != spec["buffer"]:
                    replacement = _FrameReference(spec["buffer"]) if self.native_renderer else FrameBuffer.attach(spec["buffer"])
                    item.buffer.close()
                    item.buffer = replacement
                    item._sequence = 0
                    self.scheduled.pop(key, None)
                    attached.append(item)
                    item.update()
                if self.states.get(key) != spec:
                    item.configure(spec)
                    self.states[key] = dict(spec)
                mapped = _pixel_placement(spec["placement"], self.grid, viewport) if spec["placement"] else None
                visible = bool(mapped and min(mapped[1].width, mapped[1].height) > 0)
                if visible:
                    item.place(*mapped)
                    item.raise_()  # Scene order is the TUI's paint order.
                item.setVisible(visible)
            if self.native_renderer:
                self._configure_native(state)
            for item in attached:
                item.emit(CanvasEvent("buffer_attached", {"buffer": item.buffer.info}))
            for retired in state.get("retired", ()):
                self.emit(CanvasEvent("detached", {"canvas_id": retired["id"], "canvas_token": retired["token"]}))

        def _configure_native(self, state):
            from . import _native_gpu
            luids = {(s["buffer"].adapter_low, s["buffer"].adapter_high) for s in state["items"]
                     if isinstance(s["buffer"], GpuFrameInfo)}
            if len(luids) > 1:
                raise RuntimeError("all GPU frame sources in a layer must use the same adapter LUID")
            luid = next(iter(luids), self.renderer_luid or (0, 0))
            hwnd = int(self.winId())
            if self.renderer is None or self.renderer_luid != luid or self.renderer_hwnd != hwnd:
                previous, self.renderer = self.renderer, None
                if previous:
                    previous.close()
                self.renderer = _native_gpu.Compositor(hwnd, *luid)
                self.renderer_luid = luid
                self.renderer_hwnd = hwnd
                self._native_counts.clear()
                self._native_metrics_time = time.perf_counter()
            views, mask = [], QRegion()
            dpr = self.devicePixelRatioF()
            for spec in state["items"]:
                item, info = self.items[spec["id"]], spec["buffer"]
                full, clip = item.full, item.clipped
                active = spec["placement"] is not None and item.isVisibleTo(self)
                if active:
                    mask |= QRegion(item.geometry())
                def relative(rect):
                    return (rect.x - self.clipped.x, rect.y - self.clipped.y, rect.width, rect.height)
                gpu = isinstance(info, GpuFrameInfo)
                views.append({"id": spec["id"], "gpu": gpu, "name": info.name, "width": info.width,
                              "height": info.height, "stride": getattr(info, "stride", 0),
                              "adapter_low": getattr(info, "adapter_low", 0), "adapter_high": getattr(info, "adapter_high", 0),
                              "pixel_format": info.pixel_format, "full": relative(full) if active else (0, 0, 0, 0),
                              "clip": relative(clip) if active else (0, 0, 0, 0), "fit": spec["fit"], "smooth": spec["smooth"]})
            # DirectComposition transparency does not implement per-pixel input
            # transparency. A native union region preserves TUI input holes.
            self.setMask(mask if not mask.isEmpty() else QRegion(QRect(-2, -2, 1, 1)))
            self.renderer.configure(views, max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
            names = {spec["buffer"].name for spec in state["items"]}
            self._native_counts = {name: count for name, count in self._native_counts.items() if name in names}

        def setVisible(self, visible):
            super().setVisible(visible)
            if getattr(self, "renderer", None):
                self.renderer.visible(bool(visible))

        def animate(self):
            if self.native_renderer:
                for item in self.items.values():
                    if item.isVisible():
                        item.request_focus()
                now = time.perf_counter()
                if self.renderer and now - self._native_metrics_time >= 1:
                    stats = self.renderer.metrics()
                    if stats["error"]:
                        raise RuntimeError(stats["error"])
                    counts = {}
                    for metrics in stats["views"]:
                        key = metrics.pop("id")
                        if key not in self.items:
                            continue
                        frames = metrics.pop("frames")
                        name = self.items[key].buffer.info.name
                        previous = self._native_counts.get(name, 0)
                        if frames < previous:
                            previous = 0
                        counts[name] = frames
                        rate = max(0, frames - previous) / (now - self._native_metrics_time)
                        metrics.update(painted_fps=rate if self.items[key].isVisible() else 0, source_fps=rate,
                                       draw_ms=stats["submit_ms"], presents=stats["presents"], renderer="d3d11")
                        self.items[key].emit(CanvasEvent("metrics", metrics))
                    self._native_counts.update(counts)
                    self._native_metrics_time = now
                return
            for key, item in self.items.items():
                if item.isVisible():
                    item.request_focus()
                    now = time.perf_counter()
                    seq, deadline = self.scheduled.get(key, (-1, 0))
                    if now >= deadline and item.buffer.sequence != seq:
                        self.scheduled[key] = (item.buffer.sequence, now + 1 / self.states[key]["fps"])
                        item.update()  # Qt merges dirty regions and composes in C++.
                item.report_metrics()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
            painter.end()

        def _tui_mouse(self, event, phase, button=None):
            x, y = event.position().x(), event.position().y()
            data = {"x": int(x * self.grid[0] / max(1, self.width())),
                    "y": int(y * self.grid[1] / max(1, self.height())),
                    "phase": phase, "button": button if button is not None else
                    {1: 0, 4: 1, 2: 2}.get(event.button().value, 0)}
            self.emit(CanvasEvent("tui_mouse", {"event": data}))
            if self.api and phase == "release":
                self.api.user.SetForegroundWindow(self.owner)
            event.accept()

        def mousePressEvent(self, event):
            self._tui_mouse(event, "press")

        def mouseReleaseEvent(self, event):
            self._tui_mouse(event, "release")

        def mouseMoveEvent(self, event):
            self._tui_mouse(event, "move", 0)

        def wheelEvent(self, event):
            self._tui_mouse(event, "scroll", 64 if event.angleDelta().y() > 0 else 65)

        def closeEvent(self, event):
            event.ignore()
            for item in self.items.values():
                item.emit(CanvasEvent("close_request"))

        def changeEvent(self, event):
            if event.type() == QEvent.Type.WindowStateChange:
                for item in getattr(self, "items", {}).values():
                    item.emit(CanvasEvent("window_state", {"state": self.windowState().value,
                                                           "old_state": event.oldState().value}))
            super().changeEvent(event)

        def shutdown(self):
            self.hide()
            renderer, self.renderer = self.renderer, None
            if renderer:
                renderer.close()
            for item in self.items.values():
                item.buffer.close()
            self.items.clear()
            self.destroy()

    return OverlayLayer


def run_overlay(host, commands, events, stop, parent_pid):
    """One Qt main thread, one owned HWND, many logical surfaces."""
    sink = _EventSink(events)
    tracker, window = None, None
    try:
        from ._win32_host import HostTracker, Win32
        import ctypes
        api = Win32()
        api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication
        application = QApplication([])
        application.setQuitOnLastWindowClosed(False)
        from .gpu import gpu_available
        backend = os.environ.get("COMFORTABLE_TUI_OVERLAY_BACKEND", "auto")
        if backend not in ("auto", "native", "qt"):
            raise RuntimeError("COMFORTABLE_TUI_OVERLAY_BACKEND must be auto, native or qt")
        native = gpu_available() and application.platformName() == "windows" and backend != "qt"
        if backend == "native" and not native:
            raise RuntimeError("native overlay backend requires Windows and COMFORTABLE_TUI_BUILD_GPU=1 extension")
        window = _make_layer_class()(sink.emit, host.hwnd, native=native)
        state, applied, last_error = None, None, ""
        started = time.perf_counter()
        calibrated, closing, decision = False, False, None

        def tick():
            nonlocal state, applied, tracker, last_error, closing, calibrated, decision
            if closing:
                return
            try:
                if stop.is_set() or os.getppid() != parent_pid or not api.user.IsWindow(host.hwnd):
                    closing = True
                    application.quit()
                    return
                for _ in range(64):
                    try:
                        state = commands.get_nowait()
                    except queue.Empty:
                        break
                if state is None:
                    sink.flush()
                    return
                if tracker is None:
                    tracker = HostTracker(host, state["grid"])
                tracker.grid = state["grid"]
                snapshot = tracker.snapshot()
                if snapshot.error != last_error:
                    last_error = snapshot.error
                    sink.emit(CanvasEvent("host_status", {"error": last_error, "available": snapshot.available}))
                visible = (snapshot.available and snapshot.viewport is not None and
                           tracker.visible(int(window.winId())) and any(s["placement"] for s in state["items"]))
                if snapshot.available and snapshot.viewport is not None:
                    if applied != (state, snapshot.viewport):
                        window.apply(state, snapshot.viewport)
                        applied = (state, snapshot.viewport)
                    calibrated = True
                window.setVisible(bool(visible))
                current = (bool(visible), api.user.GetForegroundWindow(), int(window.winId()), snapshot.available)
                if current != decision:
                    decision = current
                    sink.emit(CanvasEvent("layer_status", {"visible": current[0], "foreground": current[1],
                        "hwnd": current[2], "host": host.hwnd, "qt_visible": window.isVisible(),
                        "available": snapshot.available, "items": len(state["items"])}))
                if visible:
                    api.keep_above_owner(int(window.winId()), host.hwnd)
                    window.animate()
                elif not snapshot.available and time.perf_counter() - started > 10 and not calibrated:
                    raise RuntimeError(snapshot.error or "terminal viewport calibration timed out")
                sink.flush()
            except (EOFError, BrokenPipeError):
                closing = True
                application.quit()
            except Exception as exc:
                sink.emit(CanvasEvent("error", {"message": str(exc), "fatal": True}))
                sink.flush()
                closing = True
                application.quit()

        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(4)  # Item-specific deadlines cap rendering to each requested FPS.
        timer.timeout.connect(tick)
        timer.start()
        application.exec()
    except BaseException as exc:
        sink.emit(CanvasEvent("error", {"message": str(exc), "traceback": traceback.format_exc(), "fatal": True}))
        sink.flush()
    finally:
        if window:
            window.shutdown()
        if tracker:
            tracker.close()
        commands.close()
        events.cancel_join_thread()
