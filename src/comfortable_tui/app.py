"""Application clock, focus/event routing, modals, timers and worker handoff."""
from __future__ import annotations

import heapq
import itertools
import math
import os
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ._highlight import AnimatedHighlight
from ._pacing import FramePacer
from .animation import Animator, Value
from .canvas import Canvas, Rect
from .events import Capability, Key, Mouse
from .renderer import FrameTiming, Renderer
from .style import Theme
from .terminal import Terminal
from .widget import Widget


@dataclass(order=True)
class Timer:
    deadline: float
    sequence: int
    callback: object = field(compare=False)
    interval: float = field(default=0, compare=False)
    cancelled: bool = field(default=False, compare=False)

    def cancel(self):
        self.cancelled = True


class App:
    def __init__(self, root: Widget, *, theme: Theme | None = None, fps=60,
                 reduced_motion: bool | None = None, colors: str | None = None,
                 synchronized: bool | None = None, mouse=True, motion=None):
        if not 1 <= fps <= 240:
            raise ValueError("fps must be between 1 and 240")
        self.root, self.theme, self.fps, self.mouse = root, theme or Theme(), fps, mouse
        if reduced_motion is None:
            reduced_motion = os.environ.get("COMFORTABLE_TUI_REDUCED_MOTION") == "1"
        self.animator = Animator(reduced_motion, motion=motion or os.environ.get("COMFORTABLE_TUI_MOTION"))
        self._focus_highlight = AnimatedHighlight(
            lambda initial, **kwargs: self.animator.add(Value(initial, **kwargs)))
        self._focus_clip = Rect()
        self.renderer = Renderer(colors=colors or ("none" if "NO_COLOR" in os.environ else "truecolor"),
                                 synchronized=bool(synchronized))
        self._sync_override = synchronized
        self.focused: Widget | None = None
        self._capture: Widget | None = None
        self.keyboard_release, self.graphics_supported = False, False
        self.modal: Widget | None = None
        self._modal_focus = None
        self._dirty, self._running = True, False
        self._size = (80, 24)
        self._timers: list[Timer] = []
        self._sequence = itertools.count()
        self._clock = 0.0
        self._callbacks = queue.SimpleQueue()
        self._executor: ThreadPoolExecutor | None = None
        self._images = {}
        self._overlay_sessions = {}
        self._overlay_placements = {}
        self._event_pollers = []
        self._glass_workspaces = []
        self._terminal = None
        self._toast, self._toast_timer = "", None
        self.on_frame = None
        self._toast_motion = self.animator.add(Value(role="reveal"))
        self._toast_expiring = False
        self._modal_motion = self.animator.add(Value(role="reveal"))
        self._modal_picture = None
        self.bindings = {"ctrl+c": self.exit, "ctrl+q": self.exit}
        if root.parent is not None or root.app is not None:
            raise ValueError("application root must be unowned")
        root.mount(self)

    def invalidate(self):
        self._dirty = True

    def set_motion(self, profile):
        """Change the motion scheme without resetting positions or velocities."""
        self.animator.set_motion(profile)
        self.invalidate()

    def watch_events(self, callback):
        """Poll a nonblocking event source each tick, including hidden pages.

        The callback invalidates widgets only when actual state changes. Returns
        an idempotent unsubscribe function; no browser/frame dependency is added.
        """
        self._event_pollers.append(callback)
        active = True
        def stop():
            nonlocal active
            if not active:
                return
            active = False
            if callback in self._event_pollers:
                self._event_pollers.remove(callback)
        return stop

    def focus(self, widget):
        scope = self.modal or self.root
        if widget not in list(scope.focusables()):
            return False
        if self.focused is not widget:
            from .containers import ScrollView, Tabs
            if isinstance(widget, Tabs):
                widget._remember_content_focus(self.focused)
            if self.focused and hasattr(self.focused, "armed"):
                self.focused.armed = False
                self.focused.pressed.set(0)
            if self.focused and hasattr(self.focused, "_armed"):
                self.focused._armed = False
            self._assign_focus(widget)
            parent = widget.parent
            while parent:
                if isinstance(parent, ScrollView):
                    parent.ensure_visible(widget)
                parent = parent.parent
            self.invalidate()
        return True

    def _assign_focus(self, widget):
        previous = self.focused
        if previous is widget:
            return
        self.focused = widget
        for target, focused in ((previous, False), (widget, True)):
            callback = getattr(target, "_focus_changed", None)
            if callback:
                callback(focused)
        self.invalidate()

    def focus_next(self, direction=1):
        widgets = list((self.modal or self.root).focusables())
        if widgets:
            index = widgets.index(self.focused) if self.focused in widgets else (-1 if direction > 0 else 0)
            self.focus(widgets[(index + direction) % len(widgets)])

    def focus_menu(self) -> bool:
        """Toggle between the nearest tab bar and its last focused content."""
        from .containers import Tabs
        scope = self.modal or self.root
        menus = [widget for widget in scope.focusables() if isinstance(widget, Tabs)]
        widget = self.focused
        while widget:
            if widget in menus:
                return widget.focus_content() if widget is self.focused else self.focus(widget)
            widget = widget.parent
        return self.focus(menus[0]) if menus else False

    def focus_direction(self, direction: str) -> bool:
        """Focus the nearest visible neighbor after a widget declines an arrow key.

        Aligned candidates take priority over diagonal ones. Containers are
        excluded unless they have a separate, non-overlapping focus region.
        """
        if direction not in ("left", "right", "up", "down"):
            raise ValueError("direction must be left, right, up, or down")
        scope = self.modal or self.root
        widgets = list(scope.focusables())
        if self.focused not in widgets:
            return False

        def bounds(widget):
            rect = widget.focus_rect().intersect(Rect(0, 0, *self._size))
            parent = widget.parent
            while parent:
                rect = rect.intersect(parent.rect)
                parent = parent.parent
            return rect

        ancestors = set()
        parent = self.focused
        while parent:
            ancestors.add(parent)
            parent = parent.parent
        source = bounds(self.focused)
        if not source.width or not source.height:
            return False
        horizontal = direction in ("left", "right")
        sign = 1 if direction in ("right", "down") else -1
        start = source.x if horizontal else source.y
        length = source.width if horizontal else source.height
        cross_start = source.y if horizontal else source.x
        cross_length = source.height if horizontal else source.width
        candidates = []
        for index, widget in enumerate(widgets):
            if widget is self.focused:
                continue
            parent = widget.parent
            while parent and parent is not self.focused:
                parent = parent.parent
            related = widget in ancestors or parent is self.focused
            rect = bounds(widget)
            if not rect.width or not rect.height:
                continue
            intersection = rect.intersect(source)
            if related and intersection.width and intersection.height:
                continue
            other = rect.x if horizontal else rect.y
            size = rect.width if horizontal else rect.height
            cross = rect.y if horizontal else rect.x
            cross_size = rect.height if horizontal else rect.width
            forward = sign * (other + size / 2 - start - length / 2)
            if forward <= 0:
                continue
            overlap = min(cross_start + cross_length, cross + cross_size) - max(cross_start, cross)
            if related and overlap <= 0:
                continue
            gap = max(0, other - start - length if sign > 0 else start - other - size)
            cross_distance = abs(cross + cross_size / 2 - cross_start - cross_length / 2)
            candidates.append(((overlap <= 0, gap, cross_distance, forward, index), widget))
        if not candidates:
            return False
        return self.focus(min(candidates, key=lambda item: item[0])[1])

    def bind(self, key: str, callback):
        self.bindings[key] = callback

    def show_modal(self, widget: Widget):
        if widget.parent is not None or widget.app is not None:
            raise ValueError("modal must be unowned")
        if self.modal:
            self.close_modal()
        self._modal_focus = self.focused
        self.modal = widget
        self._modal_picture = None
        self._modal_motion.jump(0)
        self._modal_motion.set(1)
        widget.mount(self)
        self._assign_focus(None)
        self.invalidate()

    def close_modal(self):
        if self.modal:
            self.modal.unmount()
            self.modal = None
            self._modal_motion.set(0)
            self._assign_focus(self._modal_focus)
            self._capture = None
            self.invalidate()

    def notify(self, text: str, duration=3.0):
        self._toast = text
        self._toast_expiring = False
        self._toast_motion.set(1)
        if self._toast_timer:
            self._toast_timer.cancel()
        def clear():
            self._toast_expiring = True
            self._toast_motion.set(0)
            self.invalidate()
        self._toast_timer = self.call_later(duration, clear)
        self.invalidate()

    def call_later(self, delay: float, callback, *, repeat=False) -> Timer:
        if not math.isfinite(delay) or delay < 0 or repeat and delay <= 0:
            raise ValueError("delay must be nonnegative; repeating timers need a positive delay")
        timer = Timer(self._clock + delay, next(self._sequence), callback, delay if repeat else 0)
        heapq.heappush(self._timers, timer)
        return timer

    def call_from_thread(self, callback):
        self._callbacks.put(callback)

    def run_worker(self, function, on_result, *, on_error=None):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="comfortable")
        future = self._executor.submit(function)
        def completed(result):
            try:
                value = result.result()
            except Exception as exc:
                if on_error:
                    self.call_from_thread(lambda exc=exc: on_error(exc))
                else:
                    self.call_from_thread(lambda exc=exc: self.notify(f"Worker failed: {exc}"))
            else:
                self.call_from_thread(lambda: on_result(value))
        future.add_done_callback(completed)
        return future

    def dispatch(self, event):
        if isinstance(event, Capability):
            if event.name == "synchronized" and self._sync_override is None:
                self.renderer.synchronized = event.supported
            elif event.name == "kitty_graphics":
                self.graphics_supported = event.supported
            elif event.name == "kitty_keyboard" and event.supported:
                self.keyboard_release = True
                if self._terminal:
                    self._terminal.enable_keyboard()
            self.invalidate()
            return
        if isinstance(event, Key):
            if event.name in self.bindings:
                if event.phase == "press":
                    self.bindings[event.name]()
                return
            if event.name == "f6":
                if event.phase == "press":
                    self.focus_menu()
                return
            if event.name in ("tab", "shift+tab"):
                if event.phase != "release":
                    self.focus_next(-1 if event.name == "shift+tab" else 1)
                return
            if self.modal and event.name == "escape" and event.phase == "press":
                self.close_modal()
                return
        scope = self.modal or self.root
        if isinstance(event, Mouse):
            target = self._capture if event.phase in ("move", "release") and self._capture else scope.hit_test(event.x, event.y)
            if event.phase == "press":
                self._capture = target
                if target:
                    self.focus(target)
            if event.phase == "release":
                self._capture = None
        else:
            target = self.focused or scope
        while target:
            if not target.disabled and target.on_event(event):
                self.invalidate()
                return
            target = target.parent
        if isinstance(event, Key) and event.phase != "release" and event.name in ("left", "right", "up", "down"):
            self.focus_direction(event.name)

    def _advance(self, dt):
        for callback in tuple(self._event_pollers):
            callback()
        for session in list(self._overlay_sessions.values()):
            session.poll()
        self._clock += dt
        while not self._callbacks.empty():
            self._callbacks.get()()
            self.invalidate()
        # Snapshot due timers so call_later(0) inside callbacks runs next tick.
        due = []
        while self._timers and self._timers[0].deadline <= self._clock:
            due.append(heapq.heappop(self._timers))
        for timer in due:
            if timer.cancelled:
                continue
            timer.callback()
            self.invalidate()
            if timer.interval and not timer.cancelled:
                timer.deadline = self._clock + timer.interval
                heapq.heappush(self._timers, timer)
        self.root.tick(dt)
        if self.modal:
            self.modal.tick(dt)

    def render(self, width=80, height=24, *, dt=1 / 60) -> Canvas:
        """Deterministic headless frame; no terminal required. dt is seconds."""
        if width < 0 or height < 0 or not math.isfinite(dt) or dt < 0:
            raise ValueError("dimensions and dt must be nonnegative")
        self._advance(dt)
        return self._compose(width, height, dt)

    def _compose(self, width, height, dt):
        self._size = (width, height)
        self.root.layout(Rect(0, 0, width, height))
        if self.modal:
            w, h = min(width, self.modal.width or 56), min(height, self.modal.height or 12)
            self.modal.layout(self._modal_rect(width, height, w, h))
        scope = self.modal or self.root
        focusable = list(scope.focusables())
        if self.focused not in focusable:
            self._assign_focus(focusable[0] if focusable else None)
        self._prepare_focus(width, height)
        if self.animator.step(dt):
            self._dirty = True
        # A retired layer has no visible overshoot below zero. Release its
        # snapshot and native occlusion at the first zero crossing, rather than
        # waiting for an invisible mathematical spring tail to settle.
        if self.modal is None and self._modal_motion.target == 0 and self._modal_motion.current <= 0:
            self._modal_motion.snap()
            self._modal_picture = None
        if self._toast_expiring and self._toast_motion.current <= 0:
            self._toast_motion.snap()
        # Reflow from target geometry after advancing animated offsets.
        self.root.layout(Rect(0, 0, width, height))
        if self.modal:
            w, h = min(width, self.modal.width or 56), min(height, self.modal.height or 12)
            self.modal.layout(self._modal_rect(width, height, w, h))
        self._prepare_focus(width, height)
        frame = Canvas(width, height, self.theme.background)
        self.root.draw(frame)
        if self.modal or (self._modal_picture and self._modal_motion.active):
            # Background image placements must not cover a cell modal.
            frame.graphics.clear()
            frame.overlays.clear()
            amount = min(1, max(0, self._modal_motion.current))
            frame.tint(0, 0, width, height, self.theme.muted.with_(bg=self.theme.background.bg), .28 * amount)
            if self.modal:
                self.modal.draw(frame)
                r = self.modal.rect
                self._modal_picture = Canvas(r.width, r.height)
                self._modal_picture.blit(frame, r)
            else:
                picture = self._modal_picture
                r = self._modal_rect(width, height, picture.width, picture.height)
                frame.blit(picture, x=r.x, y=r.y, opacity=amount)
        elif not self.modal:
            self._modal_picture = None
        with frame.clipped(self._focus_clip):
            highlight = self.theme.selection.with_(reverse=self.renderer.colors == "none")
            press = getattr(self.focused, "pressed", None)
            self._focus_highlight.paint(frame, highlight, press=press.current if isinstance(press, Value) else 0)
            if self.focused:
                self.focused.paint_focus(frame)
        if self._toast_expiring and not self._toast_motion.active:
            self._toast = ""
        if self._toast and height:
            frame.graphics = {key: value for key, value in frame.graphics.items()
                              if value[2].y + value[2].height < height}
            frame.overlays = {key: (widget, rect, clip.intersect(Rect(0, 0, width, height - 1)))
                              for key, (widget, rect, clip) in frame.overlays.items()}
            amount = min(1, max(0, self._toast_motion.current))
            inset = (1 - self._toast_motion.current) * min(8, width / 8)
            frame.tint(max(0, inset), height - 1, max(0, width - 2 * max(0, inset)), 1,
                       self.theme.selection, amount)
            style = self.theme.background.blend(self.theme.selection, amount)
            frame.text(1, height - 1, self._toast, style, max(0, width - 2))
        self._dirty = False
        self._overlay_placements = frame.overlays
        return frame

    def _modal_rect(self, width, height, w, h):
        shift = round((1 - self._modal_motion.current) * min(3, height / 6))
        return Rect((width - w) // 2, max(0, min(height - h, (height - h) // 2 + shift)), w, h)

    def _sync_overlays(self):
        from .overlay import _OverlaySession
        if not self._overlay_sessions:
            widget = next((w for w, _, _ in self._overlay_placements.values() if not w._closed), None)
            if widget is None:
                return
            self._overlay_sessions["layer"] = _OverlaySession(widget)
        self._overlay_sessions["layer"].sync(self._overlay_placements, self._size)

    def _close_overlays(self):
        for session in self._overlay_sessions.values():
            session.close()
            for widget in session.widgets.values():
                widget._session = None
        self._overlay_sessions.clear()
        self._native_overlay_host = None

    def _prepare_focus(self, width, height):
        widget = self.focused
        if widget is None or not widget.focus_indicator:
            self._focus_highlight.hide()
            return
        clip = Rect(0, 0, width, height)
        parent = widget.parent
        while parent:
            clip = clip.intersect(parent.rect)
            parent = parent.parent
        goal = widget.focus_rect().intersect(clip)
        self._focus_clip = clip
        if goal.width and goal.height:
            self._focus_highlight.move_to(goal.x, goal.y, goal.width, goal.height)
        else:
            self._focus_highlight.hide()

    def encode(self, frame: Canvas) -> str:
        """Encode cells and managed images into one synchronized transaction."""
        sync = self.renderer.synchronized
        self.renderer.synchronized = False
        try:
            output = [self.renderer.encode(frame)]
        finally:
            self.renderer.synchronized = sync
        for image_id, encoder in self._images.items():
            if image_id not in frame.graphics:
                output.append(encoder.delete())
        for image_id, (encoder, surface, rect) in frame.graphics.items():
            output.append(encoder.encode(surface, rect))
        self._images = {key: value[0] for key, value in frame.graphics.items()}
        data = "".join(output)
        if data and sync:
            data = "\x1b[?2026h" + data + "\x1b[?2026l"
        return data

    def exit(self):
        self._running = False

    def run(self):
        self._running = True
        self.renderer.invalidate()
        previous = time.perf_counter()
        observed_previous = time.perf_counter()
        try:
            with Terminal(mouse=self.mouse) as terminal:
                self._terminal = terminal
                pacer = FramePacer(time.perf_counter())
                try:
                    while self._running:
                        observer = self.on_frame
                        observed_start = time.perf_counter() if observer else 0
                        start = time.perf_counter()
                        interval = start - previous
                        dt, previous = min(0.25, interval), start
                        width, height = terminal.size
                        if (width, height) != self._size:
                            self.renderer.invalidate()
                            self.invalidate()
                        # Tick once even when idle; settled frames do not allocate canvases.
                        self._advance(dt)
                        ticked = time.perf_counter() if observer else 0
                        rendered = self._dirty or self.animator.active
                        composed = encoded = written = synced = ticked
                        if rendered:
                            frame = self._compose(width, height, dt)
                            composed = time.perf_counter() if observer else 0
                            output = self.encode(frame)
                            encoded = time.perf_counter() if observer else 0
                            terminal.write(output)
                            written = time.perf_counter() if observer else 0
                            self._sync_overlays()
                            synced = time.perf_counter() if observer else 0
                        if observer:
                            observer(FrameTiming(observed_start, observed_start - observed_previous, self.fps, bool(rendered),
                                ticked - observed_start, composed - ticked, encoded - composed,
                                written - encoded, synced - written))
                            observed_previous = observed_start
                        deadline = pacer.advance(time.perf_counter(), self.fps)
                        while True:
                            wait = max(0, deadline - time.perf_counter())
                            for event in terminal.read(wait):
                                self.dispatch(event)
                            if not self._running or time.perf_counter() >= deadline:
                                break
                finally:
                    try:
                        terminal.stop_input()
                    finally:
                        for workspace in list(self._glass_workspaces):
                            workspace.close()
                        self._close_overlays()
                        for encoder in self._images.values():
                            terminal.write(encoder.delete())
                        self._images.clear()
        finally:
            self._running, self._terminal = False, None
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)

    def close(self):
        self.exit()
        for workspace in list(self._glass_workspaces):
            workspace.close()
        self._close_overlays()
        self.close_modal()
        self.root.unmount()
        self._event_pollers.clear()
        for edge in self._focus_highlight.edges:
            self.animator.remove(edge)
        self.animator.remove(self._modal_motion)
        self.animator.remove(self._toast_motion)
        self._modal_picture = None
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
