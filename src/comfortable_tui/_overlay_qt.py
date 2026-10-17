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
    _coalesced = {"mouse_move", "transform", "move", "resize", "metrics", "host_status"}

    def __init__(self, output):
        self.output, self.pending, self.latest = output, deque(), {}
        self.lost = 0

    def emit(self, event):
        if event.kind in self._coalesced:
            self.latest[event.kind] = event
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
        def __init__(self, buffer, emit, owner=None):
            super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            self.buffer, self.emit, self.owner = buffer, emit, owner
            self.api = None
            self.full, self.clipped = PixelRect(), PixelRect()
            self.transform = None
            self.fit, self.smooth, self.input_mode = "contain", True, "capture"
            self.ime = {"cursor": (0, 0, 1, 20), "text": "", "position": 0, "anchor": 0}
            self._logical_focus = False
            self._want_focus = False
            self._sequence = 0
            self._frames = self._skipped = 0
            self._draw_ms = self._age_ms = 0.0
            self._metrics_time = time.perf_counter()
            self.setWindowTitle("ComfortableTUI OverlayCanvas")
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
                if self.api:
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
            if (not self._logical_focus or mode == "passthrough") and self.isActiveWindow() and self.api:
                self.api.user.SetForegroundWindow(self.owner)
                self.clearFocus()
            QApplication.inputMethod().update(Qt.InputMethodQuery.ImQueryAll)
            if changed:
                self._transform()
                self.update()

        def _transform(self):
            transform = CanvasTransform.calculate((self.buffer.info.width, self.buffer.info.height),
                                                   (self.full.width, self.full.height), self.fit,
                                                   self.devicePixelRatioF())
            if transform != self.transform:
                previous = self.transform
                self.transform = transform
                self.emit(CanvasEvent("transform", {"transform": transform, "previous": previous,
                                                     "screen_rect": self.full, "clip_rect": self.clipped}))

        def place(self, full, clipped):
            changed = full != self.full or clipped != self.clipped
            self.full, self.clipped = full, clipped
            x, y = round(clipped.x), round(clipped.y)
            width = max(1, round(clipped.x + clipped.width) - x)
            height = max(1, round(clipped.y + clipped.height) - y)
            if self.api:
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
            self.update()

        def request_focus(self):
            if self._want_focus and self.input_mode == "capture" and self.isVisible() and self.isEnabled():
                self.activateWindow()
                self.setFocus(Qt.FocusReason.OtherFocusReason)
                self._want_focus = False

        def paintEvent(self, event):
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
            return {"position": (x, y), "source_position": self.transform.source_at(x, y) if self.transform else None,
                    "inside_source": self.transform.contains_source(x, y) if self.transform else False,
                    "screen_position": (x + self.full.x, y + self.full.y)}

        def _mouse(self, kind, event):
            data = self._position(event.position())
            data.update(button=event.button().value, buttons=event.buttons().value,
                        modifiers=modifiers(event.modifiers()), native=True)
            self.emit(CanvasEvent(kind, data))
            event.accept()

        def mousePressEvent(self, event):
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


def run_overlay(host, info, commands, events, stop, parent_pid, fps):
    """Spawn target: Qt owns the process main thread; TUI owns the parent thread."""
    sink = _EventSink(events)
    buffer, tracker, window = None, None, None
    try:
        from ._win32_host import HostTracker, Win32
        import ctypes
        api = Win32()
        api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtWidgets import QApplication
        application = QApplication([])
        application.setQuitOnLastWindowClosed(False)
        buffer = FrameBuffer.attach(info)
        window = _make_window_class()(buffer, sink.emit, host.hwnd)
        sink.emit(CanvasEvent("ready", {"hwnd": int(window.winId()), "pid": os.getpid(), "host_hwnd": host.hwnd,
                                       "host_class": api.class_name(host.hwnd)}))
        sink.emit(CanvasEvent("buffer_attached", {"buffer": info}))
        state, applied, scheduled, last_error = None, None, -1, ""
        started = time.perf_counter()
        closing = False

        def tick():
            nonlocal state, applied, buffer, tracker, scheduled, last_error, closing
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
                if state["buffer"] != buffer.info:
                    replacement = FrameBuffer.attach(state["buffer"])
                    window.buffer = replacement
                    buffer.close()
                    buffer = replacement
                    window._sequence, scheduled = 0, -1
                    window._transform()
                    sink.emit(CanvasEvent("buffer_attached", {"buffer": buffer.info}))
                if state != applied:
                    window.configure(state)
                    applied = state
                snapshot = tracker.snapshot()
                if snapshot.error != last_error:
                    last_error = snapshot.error
                    sink.emit(CanvasEvent("host_status", {"error": last_error,
                                                           "available": snapshot.available}))
                placement = state["placement"]
                visible = (placement is not None and snapshot.available and snapshot.viewport is not None
                           and tracker.visible(int(window.winId())))
                if visible:
                    mapped = _pixel_placement(placement, state["grid"], snapshot.viewport)
                    visible = mapped is not None and min(mapped[1].width, mapped[1].height) > 0
                    if visible:
                        window.place(*mapped)
                window.setVisible(bool(visible))
                if visible:
                    api.keep_above_owner(int(window.winId()), host.hwnd)
                    window.request_focus()
                    seq = buffer.sequence
                    if seq != scheduled:
                        scheduled = seq
                        window.update()
                elif not snapshot.available and time.perf_counter() - started > 10 and not window.transform:
                    raise RuntimeError(snapshot.error or "terminal viewport calibration timed out")
                window.report_metrics()
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
        timer.setInterval(max(1, round(1000 / fps)))
        timer.timeout.connect(tick)
        timer.start()
        application.exec()
    except BaseException as exc:
        sink.emit(CanvasEvent("error", {"message": str(exc), "traceback": traceback.format_exc(), "fatal": True}))
        sink.flush()
    finally:
        if window:
            window.hide()
            window.destroy()
        if tracker:
            tracker.close()
        if buffer:
            buffer.close()
        commands.close()
        # The parent may already have stopped draining input during shutdown.
        events.cancel_join_thread()
