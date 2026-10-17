"""Retained widget tree with target-based layout and explicit animation ownership."""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator
from functools import lru_cache

from .animation import Value
from .canvas import Canvas, Rect
from .events import Event
from .style import Style, Theme

if TYPE_CHECKING:
    from .app import App


@lru_cache(maxsize=512)
def _on_surface(style, background):
    return style if style.bg == background else style.with_(bg=background)


class Widget:
    focusable = False
    focus_indicator = True

    def __init__(self, *, id: str = "", width: int | None = None, height: int | None = None,
                 flex: float = 1, style: Style | None = None, disabled: bool = False,
                 visible: bool = True):
        self.id, self.width, self.height, self.flex = id, width, height, max(0, flex)
        self.style, self.disabled, self.visible = style, disabled, visible
        self.parent: Widget | None = None
        self.app: App | None = None
        self.children: list[Widget] = []
        self.rect, self.target_rect = Rect(), Rect()
        self._values: list[Value] = []

    @property
    def theme(self) -> Theme:
        return self.app.theme if self.app else Theme()

    @property
    def focused(self) -> bool:
        return self.app is not None and self.app.focused is self

    def value(self, initial=0.0, *, speed=18.0, damping=1.0, role=None) -> Value:
        v = Value(initial, speed, damping, role=role)
        self._values.append(v)
        if self.app:
            self.app.animator.add(v)
        return v

    def release_value(self, value):
        self._values.remove(value)
        if self.app:
            self.app.animator.remove(value)

    def mount(self, app: App):
        if self.app is app:
            return
        if self.app:
            self.unmount()
        self.app = app
        for value in self._values:
            app.animator.add(value)
        for child in self.children:
            child.mount(app)

    def unmount(self):
        for child in self.children:
            child.unmount()
        if self.app:
            for value in self._values:
                self.app.animator.remove(value)
            if self.app.focused is self:
                self.app.focused = None
        self.app = None

    def add(self, *children: Widget):
        for child in children:
            if child is self or any(node is self for node in child.walk()):
                raise ValueError("widget trees cannot contain cycles")
            if child.parent is not None or child.app is not None:
                raise ValueError("a widget may have only one owner")
            self.children.append(child)
            child.parent = self
            if self.app:
                child.mount(self.app)
        self.invalidate()
        return self

    def remove(self, child: Widget):
        self.children.remove(child)
        child.unmount()
        child.parent = None
        self.invalidate()

    def walk(self) -> Iterator[Widget]:
        yield self
        for child in self.children:
            yield from child.walk()

    def focusables(self) -> Iterator[Widget]:
        if not self.visible or self.disabled:
            return
        if self.focusable and self.rect.width > 0 and self.rect.height > 0:
            yield self
        for child in self.children:
            yield from child.focusables()

    def find(self, id: str) -> Widget:
        for widget in self.walk():
            if widget.id == id:
                return widget
        raise KeyError(id)

    def invalidate(self):
        if self.app:
            self.app.invalidate()

    def measure(self, width: int) -> int:
        return self.height if self.height is not None else 1

    def layout(self, rect: Rect):
        self.rect = self.target_rect = rect

    def tick(self, dt: float):
        for child in self.children:
            if child.visible:
                child.tick(dt)

    def paint(self, canvas: Canvas):
        pass

    def paint_focus(self, canvas: Canvas):
        """Optional feedback painted above the shared focus highlight."""
        pass

    def content_style(self, canvas: Canvas, semantic: Style | None = None) -> Style:
        """Inherit the painted parent surface unless an explicit widget style is supplied."""
        if self.style is not None:
            return self.style
        area = self.rect.intersect(canvas.clip)
        base = canvas.style_at(area.x, area.y, self.theme.background)
        return _on_surface(semantic or self.theme.background, base.bg)

    def focus_rect(self) -> Rect:
        return self.rect

    def draw(self, canvas: Canvas):
        if not self.visible or self.rect.width <= 0 or self.rect.height <= 0:
            return
        with canvas.clipped(self.rect):
            if not canvas.clip.width or not canvas.clip.height:
                return
            self.paint(canvas)
            for child in self.children:
                child.draw(canvas)

    def on_event(self, event: Event) -> bool:
        return False

    def hit_test(self, x: int, y: int) -> Widget | None:
        if not self.visible or self.disabled or not self.rect.contains(x, y):
            return None
        for child in reversed(self.children):
            hit = child.hit_test(x, y)
            if hit:
                return hit
        return self if self.focusable else None
