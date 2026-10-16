"""Compositional layout, scroll view, tabs, collapsible sections and page stack."""
from __future__ import annotations

import math

from .canvas import Rect, text_width
from .events import Key, Mouse
from .widget import Widget


class Stack(Widget):
    horizontal = False

    def __init__(self, *children, gap=0, padding=0, **kwargs):
        super().__init__(**kwargs)
        self.gap, self.padding = max(0, gap), max(0, padding)
        self.add(*children)

    def measure(self, width):
        if self.height is not None:
            return self.height
        children = [c for c in self.children if c.visible]
        sizes = [c.measure(max(0, width - self.padding * 2)) for c in children]
        return self.padding * 2 + (max(sizes, default=0) if self.horizontal else
                                   sum(sizes) + self.gap * max(0, len(sizes) - 1))

    def layout(self, rect):
        super().layout(rect)
        area = rect.inset(self.padding)
        children = [c for c in self.children if c.visible]
        total = area.width if self.horizontal else area.height
        available = max(0, total - self.gap * max(0, len(children) - 1))
        fixed = [c.width if self.horizontal else c.height for c in children]
        remainder = max(0, available - sum(s for s in fixed if s is not None))
        weights = sum(c.flex for c, s in zip(children, fixed) if s is None)
        pos, used_weight, allocated = 0, 0.0, 0
        for child, size in zip(children, fixed):
            if size is None:
                used_weight += child.flex
                end = round(remainder * used_weight / weights) if weights else 0
                size, allocated = end - allocated, end
            size = min(max(0, size), max(0, total - pos))
            child.layout(Rect(area.x + pos, area.y, size, area.height) if self.horizontal else
                         Rect(area.x, area.y + pos, area.width, size))
            pos += size + self.gap


class VBox(Stack):
    pass


class HBox(Stack):
    horizontal = True


class Grid(Widget):
    def __init__(self, *children, columns=2, gap=1, **kwargs):
        super().__init__(**kwargs)
        if columns < 1:
            raise ValueError("columns must be positive")
        self.columns, self.gap = columns, max(0, gap)
        self.add(*children)

    def layout(self, rect):
        super().layout(rect)
        children = [c for c in self.children if c.visible]
        rows = math.ceil(len(children) / self.columns)
        if not rows:
            return
        w = max(0, rect.width - self.gap * (self.columns - 1))
        h = max(0, rect.height - self.gap * (rows - 1))
        for i, child in enumerate(children):
            row, col = divmod(i, self.columns)
            x, right = round(w * col / self.columns), round(w * (col + 1) / self.columns)
            y, bottom = round(h * row / rows), round(h * (row + 1) / rows)
            child.layout(Rect(rect.x + x + col * self.gap, rect.y + y + row * self.gap,
                              right - x, bottom - y))


class Panel(Widget):
    def __init__(self, child: Widget, title="", *, padding=0, **kwargs):
        super().__init__(**kwargs)
        self.title, self.padding = title, max(0, padding)
        self.add(child)

    def measure(self, width):
        return self.height if self.height is not None else self.children[0].measure(
            max(0, width - 2 - self.padding * 2)) + 2 + self.padding * 2

    def layout(self, rect):
        super().layout(rect)
        self.children[0].layout(rect.inset(1 + self.padding))

    def paint(self, canvas):
        canvas.fill(self.rect, self.style or self.theme.panel)
        focused = self.app and any(c is self.app.focused for c in self.walk())
        canvas.box(self.rect, self.theme.accent if focused else self.theme.muted, self.title)


class ScrollView(Widget):
    focusable = True

    def __init__(self, child: Widget, **kwargs):
        super().__init__(**kwargs)
        self.scroll = self.value()
        self.content_height = 0
        self.add(child)

    def layout(self, rect):
        super().layout(rect)
        child = self.children[0]
        self.content_height = child.measure(max(0, rect.width - 1))
        self.scroll.set(min(max(0, self.scroll.target), max(0, self.content_height - rect.height)))
        child.layout(Rect(rect.x, rect.y - round(self.scroll.current),
                          max(0, rect.width - 1), self.content_height))

    def scroll_by(self, amount):
        self.scroll.set(min(max(0, self.scroll.target + amount),
                            max(0, self.content_height - self.rect.height)))
        self.invalidate()

    def ensure_visible(self, widget):
        top = widget.rect.y - self.rect.y + round(self.scroll.current)
        if top < self.scroll.target:
            self.scroll.set(top)
        elif top + widget.rect.height > self.scroll.target + self.rect.height:
            self.scroll.set(top + widget.rect.height - self.rect.height)

    def on_event(self, event):
        if isinstance(event, Mouse) and event.phase == "scroll":
            self.scroll_by(-3 if event.button & 1 == 0 else 3)
            return True
        if isinstance(event, Key) and event.phase != "release":
            amount = {"up": -1, "down": 1, "pageup": -self.rect.height,
                      "pagedown": self.rect.height}.get(event.name)
            if amount is not None:
                self.scroll_by(amount)
                return True
        return False

    def draw(self, canvas):
        super().draw(canvas)
        if self.visible and self.content_height > self.rect.height:
            with canvas.clipped(self.rect):
                h = max(1, round(self.rect.height ** 2 / self.content_height))
                y = round((self.rect.height - h) * self.scroll.current /
                          max(1, self.content_height - self.rect.height))
                canvas.fill(Rect(self.rect.x + self.rect.width - 1, self.rect.y + y, 1, h),
                            self.theme.accent, "┃")


class Tabs(Widget):
    focusable = True

    def __init__(self, tabs: dict[str, Widget], *, on_change=None, **kwargs):
        super().__init__(**kwargs)
        if not tabs:
            raise ValueError("at least one tab required")
        self.titles, self.selected = list(tabs), 0
        self.on_change = on_change
        self.add(*tabs.values())
        self._sync()

    def _sync(self):
        for i, child in enumerate(self.children):
            child.visible = i == self.selected

    def select(self, index):
        self.selected = index % len(self.children)
        self._sync()
        if self.app and self.app.focused not in list(self.focusables()):
            self.app.focus(self)
        if self.on_change:
            self.on_change(self.selected)
        self.invalidate()

    def layout(self, rect):
        super().layout(rect)
        self.children[self.selected].layout(Rect(rect.x, rect.y + 2, rect.width, max(0, rect.height - 2)))

    def paint(self, canvas):
        x = self.rect.x
        for i, title in enumerate(self.titles):
            canvas.text(x, self.rect.y, f" {title} ", self.theme.selection if i == self.selected else self.theme.muted)
            x += text_width(title) + 3

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("left", "right"):
            self.select(self.selected + (1 if event.name == "right" else -1))
            return True
        if isinstance(event, Mouse) and event.phase == "press" and event.y == self.rect.y:
            x = self.rect.x
            for i, title in enumerate(self.titles):
                if x <= event.x < x + text_width(title) + 2:
                    self.select(i)
                    return True
                x += text_width(title) + 3
        return False


class Collapsible(Widget):
    focusable = True

    def __init__(self, title: str, child: Widget, *, expanded=True, **kwargs):
        super().__init__(**kwargs)
        self.title, self.expanded = title, expanded
        self.reveal = self.value(1.0 if expanded else 0.0)
        self.add(child)
        child.visible = expanded

    def toggle(self):
        self.expanded = not self.expanded
        self.reveal.set(1 if self.expanded else 0)
        self.children[0].visible = True
        if self.app and self.app.focused in list(self.children[0].walk()):
            self.app.focus(self)
        self.invalidate()

    def measure(self, width):
        return self.height if self.height is not None else 1 + round(
            self.children[0].measure(width) * self.reveal.current)

    def layout(self, rect):
        super().layout(rect)
        if not self.expanded and not self.reveal.active:
            self.children[0].visible = False
        self.children[0].layout(Rect(rect.x, rect.y + 1, rect.width,
                                     min(max(0, rect.height - 1), round(
                                         self.children[0].measure(rect.width) * self.reveal.current))))

    def paint(self, canvas):
        canvas.text(self.rect.x, self.rect.y, ("▾ " if self.expanded else "▸ ") + self.title,
                    self.theme.selection if self.focused else self.theme.accent)

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("enter", " "):
            self.toggle()
            return True
        if isinstance(event, Mouse) and event.phase == "release" and event.y == self.rect.y:
            self.toggle()
            return True
        return False


class PageStack(Widget):
    """Pages slide with preserved spring velocity; suspended pages keep state."""
    def __init__(self, root: Widget, **kwargs):
        super().__init__(**kwargs)
        self.offsets = {}
        self.leaving: list[Widget] = []
        self.add(root)
        self.offsets[root] = self.value()
        self.saved_focus = {}

    @property
    def top(self):
        return next(c for c in reversed(self.children) if c not in self.leaving)

    def push(self, page):
        old = self.top
        self.saved_focus[old] = self.app.focused if self.app else None
        self.offsets[old].set(-max(1, self.rect.width))
        self.add(page)
        self.offsets[page] = self.value(max(1, self.rect.width))
        self.offsets[page].set(0)
        if self.app:
            self.app.focused = None
        self.invalidate()

    def pop(self):
        if len(self.children) - len(self.leaving) <= 1:
            return False
        old = self.top
        self.leaving.append(old)
        self.offsets[old].set(max(1, self.rect.width))
        page = self.top
        self.offsets[page].set(0)
        if self.app:
            self.app.focused = self.saved_focus.get(page)
        self.invalidate()
        return True

    def focusables(self):
        if self.visible and not self.disabled:
            yield from self.top.focusables()

    def hit_test(self, x, y):
        return self.top.hit_test(x, y) if self.visible and self.rect.contains(x, y) else None

    def layout(self, rect):
        super().layout(rect)
        for child in list(self.children):
            offset = self.offsets[child]
            if child in self.leaving and not offset.active:
                self.remove(child)
                self._values.remove(offset)
                if self.app:
                    self.app.animator.remove(offset)
                del self.offsets[child]
                self.leaving.remove(child)
                self.saved_focus.pop(child, None)
                continue
            if child is not self.top and not offset.active and child not in self.leaving:
                continue
            child.layout(Rect(rect.x + round(offset.current), rect.y, rect.width, rect.height))

    def draw(self, canvas):
        if not self.visible:
            return
        with canvas.clipped(self.rect):
            for child in self.children:
                if child is self.top or self.offsets[child].active:
                    child.draw(canvas)

    def tick(self, dt):
        for child in self.children:
            if child is self.top or self.offsets[child].active:
                child.tick(dt)

    def on_event(self, event):
        if isinstance(event, Key) and event.name == "escape" and event.phase != "release":
            return self.pop()
        return False
