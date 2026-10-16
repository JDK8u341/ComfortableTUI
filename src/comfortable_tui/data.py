"""Virtualized list/table, choice controls, lazy directory and tree views."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path

from .canvas import Rect, crop_text, graphemes, text_width
from .controls import Button
from .events import Key, Mouse
from .widget import Widget


class ListView(Widget):
    focusable = True
    focus_indicator = False  # Selection already has its own animated geometry.

    def __init__(self, items=(), *, on_select=None, on_change=None, item_height=1, **kwargs):
        super().__init__(**kwargs)
        if item_height < 1:
            raise ValueError("item_height must be at least 1")
        self.items = [str(x) for x in items]
        self.selected, self.item_height = 0, item_height
        self.on_select, self.on_change = on_select, on_change
        self.scroll = self.value(speed=22, damping=0.85)
        self.cursor = self.value(speed=28, damping=0)
        self.edge = self.value(speed=22)
        self._effects = {}  # index -> [Value, deleting]
        self._prefix = []
        self._prefix_dirty = True
        self._armed = False

    def set_items(self, items):
        self.items = [str(x) for x in items]
        self.selected = min(self.selected, max(0, len(self.items) - 1))
        for value, _ in self._effects.values():
            self._release_effect(value)
        self._effects.clear()
        self._prefix_dirty = True
        self.invalidate()

    def _release_effect(self, value):
        self._values.remove(value)
        if self.app:
            self.app.animator.remove(value)

    def insert_item(self, index: int, text: str, *, animate=True):
        index = max(0, min(index, len(self.items)))
        self.items.insert(index, str(text))
        self._effects = {i + int(i >= index): e for i, e in self._effects.items()}
        if len(self.items) > 1 and index <= self.selected:
            self.selected += 1
        if animate:
            size = self.value(0, speed=18, damping=0)
            size.set(self.item_height)
            self._effects[index] = [size, False]
        self._prefix_dirty = True
        self.invalidate()

    def remove_item(self, index: int, *, animate=True):
        if not 0 <= index < len(self.items):
            raise IndexError(index)
        if animate:
            if index not in self._effects:
                self._effects[index] = [self.value(self.item_height, damping=0), True]
            self._effects[index][1] = True
            self._effects[index][0].set(0)
        else:
            self._delete(index)
        self._prefix_dirty = True
        self.invalidate()

    def _delete(self, index):
        del self.items[index]
        effect = self._effects.pop(index, None)
        if effect:
            self._release_effect(effect[0])
        self._effects = {i - int(i > index): e for i, e in self._effects.items()}
        if index < self.selected:
            self.selected -= 1
        self.selected = min(self.selected, max(0, len(self.items) - 1))

    def _rebuild(self):
        if not self._prefix_dirty and not self._effects:
            return
        self._prefix = [0.0]
        for i in range(len(self.items)):
            size = self._effects[i][0].current if i in self._effects else self.item_height
            self._prefix.append(self._prefix[-1] + size)
        self._prefix_dirty = False

    def tick(self, dt):
        if self._effects:
            for index in sorted(self._effects, reverse=True):
                value, deleting = self._effects[index]
                if not value.active:
                    if deleting:
                        self._delete(index)
                    else:
                        self._release_effect(value)
                        del self._effects[index]
            self._prefix_dirty = True

    def select(self, index):
        if not self.items:
            return
        new = min(max(0, index), len(self.items) - 1)
        if new == self.selected and new != index:
            self.edge.kick(0.7 if index > new else -0.7)
        changed = new != self.selected
        self.selected = new
        if changed and self.on_change:
            self.on_change(new)
        self.invalidate()

    def layout(self, rect):
        super().layout(rect)
        self._rebuild()
        if not self.items:
            self.scroll.set(0)
            return
        y = self._prefix[self.selected]
        end = self._prefix[self.selected + 1]
        target = self.scroll.target
        if y < target:
            target = y
        elif end > target + rect.height:
            target = end - rect.height
        self.scroll.set(min(max(0, target), max(0, self._prefix[-1] - rect.height)))
        self.cursor.set(y)

    def item_text(self, index):
        return self.items[index]

    def paint(self, canvas):
        r = self.rect
        if not self.items or not r.height:
            canvas.text(r.x, r.y, "(empty)", self.content_style(canvas, self.theme.muted), r.width)
            return
        self._rebuild()
        offset = self.scroll.current - self.edge.current
        first = max(0, bisect.bisect_right(self._prefix, offset) - 1)
        last = min(len(self.items), bisect.bisect_left(self._prefix, offset + r.height) + 1)
        # Composite against the actual parent surface, including fractional rows.
        cy = self.cursor.current - offset
        highlight = self.theme.selection.with_(reverse=bool(self.app and self.app.renderer.colors == "none"))
        canvas.tint(r.x, r.y + cy, max(0, r.width - 1), self.item_height, highlight,
                    opacity=1 if self.focused else 0.5)
        for i in range(first, last):
            if self._prefix[i + 1] - self._prefix[i] < 0.5:
                continue
            y = r.y + round(self._prefix[i] - offset)
            if not canvas.clip.y <= y < canvas.clip.y + canvas.clip.height:
                continue
            style = canvas.style_at(max(r.x, canvas.clip.x), y, self.theme.background)
            canvas.text(r.x + 2, y, self.item_text(i), self.style or style, max(0, r.width - 4))
        marker_y = math.floor(cy)
        if 0 <= marker_y < r.height:
            # Four vertical braille positions per row, independent of text rounding.
            marker = ("⠉", "⠒", "⠤", "⣀")[min(3, int((cy - marker_y) * 4))]
            bg = canvas.style_at(r.x, r.y + marker_y).bg
            canvas.put(r.x, r.y + marker_y, marker, self.theme.accent.with_(bg=bg))
        if self._prefix[-1] > r.height and r.width:
            size = max(1, round(r.height * r.height / self._prefix[-1]))
            top = round((r.height - size) * self.scroll.current / max(1, self._prefix[-1] - r.height))
            canvas.fill(Rect(r.x + r.width - 1, r.y + top, 1, size),
                        self.content_style(canvas, self.theme.accent), "┃")

    def _activate(self):
        if self.items and self.on_select:
            self.on_select(self.selected, self.items[self.selected])

    def on_event(self, event):
        if isinstance(event, Mouse):
            if event.phase == "scroll":
                self.select(self.selected + (-3 if event.button & 1 == 0 else 3))
                return True
            if event.phase == "press":
                self._rebuild()
                row = event.y - self.rect.y + self.scroll.current
                self.select(max(0, bisect.bisect_right(self._prefix, row) - 1))
                self._armed = True
                return True
            if event.phase == "release":
                if self._armed and self.rect.contains(event.x, event.y):
                    self._activate()
                self._armed = False
                return True
        if not isinstance(event, Key):
            return False
        if event.name == "enter":
            if self.app and self.app.keyboard_release:
                if event.phase == "press":
                    self._armed = True
                elif event.phase == "release" and self._armed:
                    self._armed = False
                    self._activate()
            elif event.phase == "press":
                self._activate()
            return True
        if event.phase == "release":
            return False
        delta = {"up": -1, "down": 1, "pageup": -max(1, self.rect.height // self.item_height),
                 "pagedown": max(1, self.rect.height // self.item_height)}.get(event.name)
        if delta is not None:
            self.select(self.selected + delta)
        elif event.name in ("home", "end"):
            self.select(0 if event.name == "home" else len(self.items) - 1)
        else:
            return False
        return True


class SelectionList(ListView):
    def __init__(self, items=(), *, checked=(), on_toggle=None, **kwargs):
        super().__init__(items, **kwargs)
        self.checked, self.on_toggle = set(checked), on_toggle

    def item_text(self, index):
        return f"[{'x' if index in self.checked else ' '}] " + self.items[index]

    def _activate(self):
        if not self.items:
            return
        if self.selected in self.checked:
            self.checked.remove(self.selected)
        else:
            self.checked.add(self.selected)
        if self.on_toggle:
            self.on_toggle(set(self.checked))
        self.invalidate()

    def on_event(self, event):
        if isinstance(event, Key) and event.name == " " and event.phase == "press":
            self._activate()
            return True
        return super().on_event(event)

    def _delete(self, index):
        super()._delete(index)
        self.checked = {i - int(i > index) for i in self.checked if i != index}

    def insert_item(self, index, text, *, animate=True):
        index = max(0, min(index, len(self.items)))
        self.checked = {i + int(i >= index) for i in self.checked}
        super().insert_item(index, text, animate=animate)

    def set_items(self, items):
        super().set_items(items)
        self.checked.clear()


class RadioGroup(ListView):
    def __init__(self, items=(), *, value=0, **kwargs):
        super().__init__(items, **kwargs)
        self.selected = min(max(0, value), max(0, len(self.items) - 1))
        self.cursor.jump(self.selected * self.item_height)

    def item_text(self, index):
        return f"({'●' if index == self.selected else ' '}) " + self.items[index]


class Select(Button):
    def __init__(self, options, *, value=0, on_change=None, **kwargs):
        self.options = list(map(str, options))
        if not self.options:
            raise ValueError("options cannot be empty")
        if not 0 <= value < len(self.options):
            raise ValueError("value index is out of range")
        self.selected, self.on_change = value, on_change
        super().__init__(self.options[value] + " ▾", **kwargs)

    def set_value(self, index):
        self.selected = index % len(self.options)
        self.label = self.options[self.selected] + " ▾"
        if self.on_change:
            self.on_change(self.selected, self.options[self.selected])
        self.invalidate()

    def activate(self):
        if self.app:
            from .containers import Panel
            def chosen(index, text):
                self.set_value(index)
                self.app.close_modal()
            options = ListView(self.options, on_select=chosen)
            options.select(self.selected)
            self.app.show_modal(Panel(options, "Choose", width=48, height=min(16, len(self.options) + 2)))
        else:
            self.set_value(self.selected + 1)

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("left", "right"):
            self.set_value(self.selected + (1 if event.name == "right" else -1))
            return True
        return super().on_event(event)


@dataclass
class TreeNode:
    label: str
    children: list[TreeNode] = field(default_factory=list)
    expanded: bool = False
    data: object = None


class Tree(ListView):
    def __init__(self, root: TreeNode, **kwargs):
        self.root, self.nodes = root, []
        super().__init__([], **kwargs)
        self.refresh()

    def refresh(self):
        self.nodes = []
        # Iterative traversal does not depend on Python's recursion depth.
        pending = [(self.root, 0)]
        while pending:
            node, depth = pending.pop()
            self.nodes.append((node, depth))
            if node.expanded:
                pending.extend((child, depth + 1) for child in reversed(node.children))
        self.set_items(["  " * depth + ("▾ " if node.expanded else "▸ " if node.children else "· ") + node.label
                        for node, depth in self.nodes])

    def expand(self, node):
        node.expanded = True

    def _activate(self):
        if self.nodes:
            node, _ = self.nodes[self.selected]
            if node.expanded:
                node.expanded = False
            else:
                self.expand(node)
            self.refresh()
        super()._activate()

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("left", "right") and self.nodes:
            node, depth = self.nodes[self.selected]
            if event.name == "right":
                self.expand(node)
            elif node.expanded:
                node.expanded = False
            else:
                for i in range(self.selected - 1, -1, -1):
                    if self.nodes[i][1] < depth:
                        self.selected = i
                        break
            self.refresh()
            return True
        return super().on_event(event)


class DirectoryTree(Tree):
    """Read one directory on expansion. Symlink directories are not followed."""
    def __init__(self, path=".", **kwargs):
        self.path = Path(path).resolve()
        root = TreeNode(self.path.name or str(self.path), data=self.path)
        super().__init__(root, **kwargs)
        self.expand(root)
        self.refresh()

    def expand(self, node):
        path = node.data
        if isinstance(path, Path) and path.is_dir() and not path.is_symlink() and not node.children:
            try:
                paths = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
                node.children = [TreeNode(p.name + ("/" if p.is_dir() else ""), data=p) for p in paths]
            except OSError as exc:
                node.children = [TreeNode(f"[{exc.strerror or 'Cannot read directory'}]")]
        node.expanded = bool(node.children)


class DataTable(ListView):
    def __init__(self, columns, rows=(), *, column_widths=None, **kwargs):
        self.columns = list(map(str, columns))
        if not self.columns:
            raise ValueError("columns cannot be empty")
        self.rows = [tuple(map(str, row)) for row in rows]
        self.column_widths = column_widths
        self.horizontal_offset = 0
        super().__init__(["" for _ in self.rows], **kwargs)
        self._body_rect = Rect()

    def _format(self, values):
        widths = self.column_widths or [max(4, (self.rect.width - 4) // len(self.columns) - 3)] * len(self.columns)
        parts = []
        for value, width in zip(values, widths):
            text = crop_text(value, max(0, width))
            parts.append(text + " " * max(0, width - text_width(text)))
        return " │ ".join(parts)

    def item_text(self, index):
        text = self._format(self.rows[index])
        return self._slice(text)

    def _slice(self, text):
        remaining = self.horizontal_offset
        for i, glyph in enumerate(graphemes(text)):
            size = text_width(glyph)
            if remaining < size:
                return (" " * (size - remaining) if remaining else glyph) + "".join(graphemes(text)[i + 1:])
            remaining -= size
        return ""

    def set_rows(self, rows):
        self.rows = [tuple(map(str, row)) for row in rows]
        self.set_items(["" for _ in self.rows])

    def layout(self, rect):
        # Selection visibility is based on the body, not the fixed header.
        body = Rect(rect.x, rect.y + 2, rect.width, max(0, rect.height - 2))
        super().layout(body)
        self._body_rect = body
        self.rect = self.target_rect = rect

    def paint(self, canvas):
        canvas.text(self.rect.x + 2, self.rect.y, self._slice(self._format(self.columns)),
                    self.content_style(canvas, self.theme.accent), max(0, self.rect.width - 4))
        canvas.text(self.rect.x, self.rect.y + 1, "─" * self.rect.width,
                    self.content_style(canvas, self.theme.muted))
        rect = self.rect
        self.rect = self._body_rect
        with canvas.clipped(self._body_rect):
            super().paint(canvas)
        self.rect = rect

    def _activate(self):
        if self.rows and self.on_select:
            self.on_select(self.selected, self.rows[self.selected])

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("left", "right"):
            maximum = max(0, text_width(self._format(self.columns)) - max(0, self.rect.width - 4))
            offset = min(maximum, max(0, self.horizontal_offset + (4 if event.name == "right" else -4)))
            if offset == self.horizontal_offset:
                return False
            self.horizontal_offset = offset
            self.invalidate()
            return True
        if isinstance(event, Mouse) and event.y < self._body_rect.y and event.phase != "scroll":
            return False
        rect = self.rect
        self.rect = self._body_rect
        try:
            return super().on_event(event)
        finally:
            self.rect = rect
