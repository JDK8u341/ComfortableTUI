"""Basic display, form and feedback controls."""
from __future__ import annotations


from wcwidth import wcswidth

from .canvas import clean_text, crop_text, graphemes, text_width
from .events import Key, Mouse, Paste
from .widget import Widget


class Label(Widget):
    def __init__(self, text="", *, align="left", **kwargs):
        super().__init__(**kwargs)
        self.text, self.align = str(text), align

    def set_text(self, text):
        self.text = str(text)
        self.invalidate()

    def measure(self, width):
        return self.height if self.height is not None else max(1, len(self.text.splitlines()))

    def paint(self, canvas):
        for row, line in enumerate(self.text.splitlines() or [""]):
            if row >= self.rect.height:
                break
            used = min(text_width(line), self.rect.width)
            offset = {"left": 0, "center": (self.rect.width - used) // 2,
                      "right": self.rect.width - used}.get(self.align, 0)
            canvas.text(self.rect.x + offset, self.rect.y + row, line,
                        self.style or self.theme.background, self.rect.width - offset)


class Paragraph(Label):
    """Grapheme wrapping; explicit newlines retained, long words wrap safely."""
    def lines(self, width):
        if width <= 0:
            return []
        lines, current, used = [], [], 0
        for glyph in graphemes(clean_text(self.text)):
            n = max(0, wcswidth(glyph))
            if glyph == "\n" or used + n > width:
                lines.append("".join(current))
                current, used = [], 0
                if glyph == "\n":
                    continue
            if n <= width:
                current.append(glyph)
                used += n
        lines.append("".join(current))
        return lines

    def measure(self, width):
        return self.height if self.height is not None else len(self.lines(width))

    def paint(self, canvas):
        for row, line in enumerate(self.lines(self.rect.width)[:self.rect.height]):
            canvas.text(self.rect.x, self.rect.y + row, line, self.style or self.theme.background)


class Rule(Widget):
    def paint(self, canvas):
        canvas.text(self.rect.x, self.rect.y, "─" * self.rect.width, self.style or self.theme.muted)


class Spacer(Widget):
    pass


class Button(Widget):
    focusable = True

    def __init__(self, label: str, on_press=None, **kwargs):
        super().__init__(**kwargs)
        self.label, self.on_press = label, on_press
        self.pressed = self.value(0, speed=26)
        self.armed = False

    def activate(self):
        if self.disabled:
            return
        self.pressed.kick(0.7)
        if self.on_press:
            self.on_press()
        self.invalidate()

    def paint(self, canvas):
        style = self.style or (self.theme.selection if self.focused else self.theme.panel)
        if self.disabled:
            style = self.theme.muted
        canvas.fill(self.rect, style)
        text = f"[ {self.label} ]"
        x = self.rect.x + max(0, (self.rect.width - text_width(text)) // 2)
        canvas.text(x + (1 if self.pressed.current > 0.25 else 0),
                    self.rect.y + max(0, (self.rect.height - 1) // 2), text, style, self.rect.width)

    def on_event(self, event):
        if self.disabled:
            return False
        if isinstance(event, Mouse) and event.button & 3 == 0:
            if event.phase == "press":
                self.armed = True
                self.pressed.set(0.5)
                return True
            if event.phase == "release":
                armed, self.armed = self.armed, False
                self.pressed.set(0)
                if armed and self.rect.contains(event.x, event.y):
                    self.activate()
                return True
        if isinstance(event, Key) and event.name in ("enter", " "):
            release_mode = self.app and self.app.keyboard_release
            if release_mode:
                if event.phase == "press":
                    self.armed = True
                    self.pressed.set(0.5)
                elif event.phase == "release":
                    armed, self.armed = self.armed, False
                    self.pressed.set(0)
                    if armed:
                        self.activate()
            elif event.phase == "press":
                self.activate()
            return True
        return False


class Checkbox(Button):
    def __init__(self, label: str, checked=False, *, on_change=None, **kwargs):
        super().__init__(label, **kwargs)
        self.checked, self.on_change = checked, on_change

    def activate(self):
        if self.disabled:
            return
        self.checked = not self.checked
        self.pressed.kick(0.5)
        if self.on_change:
            self.on_change(self.checked)
        self.invalidate()

    def paint(self, canvas):
        style = self.style or (self.theme.selection if self.focused else self.theme.background)
        canvas.text(self.rect.x, self.rect.y, f"[{'x' if self.checked else ' '}] {self.label}", style,
                    self.rect.width)


class Switch(Checkbox):
    def __init__(self, label: str, checked=False, **kwargs):
        super().__init__(label, checked, **kwargs)
        self.position = self.value(1 if checked else 0, speed=22)

    def activate(self):
        super().activate()
        self.position.set(1 if self.checked else 0)

    def paint(self, canvas):
        style = self.theme.selection if self.focused else self.theme.background
        canvas.text(self.rect.x, self.rect.y, "[     ] " + self.label, style, self.rect.width)
        canvas.put(self.rect.x + 1 + round(self.position.current * 4), self.rect.y,
                   "●", self.theme.success if self.checked else self.theme.muted)


class Input(Widget):
    focusable = True

    def __init__(self, value="", *, placeholder="", password=False, max_length=4096,
                 validator=None, on_change=None, on_submit=None, **kwargs):
        super().__init__(**kwargs)
        self.text = clean_text(value).replace("\n", " ")
        self.placeholder, self.password, self.max_length = placeholder, password, max_length
        self.validator, self.on_change, self.on_submit = validator, on_change, on_submit
        self.cursor = len(graphemes(self.text))
        self.error = ""
        self._offset = 0

    def set_value(self, text):
        self.text = "".join(graphemes(clean_text(text).replace("\n", " "))[:self.max_length])
        self.cursor = len(graphemes(self.text))
        self._changed()

    def _changed(self):
        self.error = ""
        if self.validator:
            result = self.validator(self.text)
            if result is False:
                self.error = "Invalid value"
            elif isinstance(result, str):
                self.error = result
        if self.on_change:
            self.on_change(self.text)
        self.invalidate()

    def _insert(self, text):
        chars = list(graphemes(self.text))
        added = list(graphemes(clean_text(text).replace("\n", " ")))
        added = added[:max(0, self.max_length - len(chars))]
        chars[self.cursor:self.cursor] = added
        self.cursor += len(added)
        self.text = "".join(chars)
        self._changed()

    def paint(self, canvas):
        r = self.rect
        style = self.style or (self.theme.selection if self.focused else self.theme.panel)
        canvas.fill(r, style)
        available = max(0, r.width - 2)
        chars = list(graphemes(self.text))
        display = ["•"] * len(chars) if self.password else chars
        cursor_col = text_width("".join(display[:self.cursor]))
        self._offset = min(self._offset, self.cursor)
        while self._offset < self.cursor and text_width("".join(display[self._offset:self.cursor])) >= max(1, available):
            self._offset += 1
        start_col = text_width("".join(display[:self._offset]))
        text = "".join(display[self._offset:]) if self.text else self.placeholder
        canvas.text(r.x + 1, r.y, text, style if self.text else self.theme.muted, available)
        if self.focused and available:
            x = r.x + 1 + min(available - 1, cursor_col - start_col)
            char = display[self.cursor] if self.cursor < len(display) else " "
            canvas.put(x, r.y, char, style.with_(reverse=True))

    def on_event(self, event):
        if isinstance(event, Paste):
            self._insert(event.text)
            return True
        if isinstance(event, Key) and event.phase != "release":
            chars = list(graphemes(self.text))
            name = event.name
            if name == "left":
                self.cursor = max(0, self.cursor - 1)
            elif name == "right":
                self.cursor = min(len(chars), self.cursor + 1)
            elif name in ("home", "ctrl+a"):
                self.cursor = 0
            elif name in ("end", "ctrl+e"):
                self.cursor = len(chars)
            elif name in ("backspace", "delete"):
                index = self.cursor - 1 if name == "backspace" else self.cursor
                if 0 <= index < len(chars):
                    del chars[index]
                    if name == "backspace":
                        self.cursor -= 1
                    self.text = "".join(chars)
                    self._changed()
            elif name == "ctrl+u":
                self.set_value("")
            elif name == "enter":
                self._changed()
                if not self.error and self.on_submit:
                    self.on_submit(self.text)
            elif event.text:
                self._insert(event.text)
            else:
                return False
            self.invalidate()
            return True
        return False


class TextArea(Widget):
    """Plain-text multiline editor with grapheme cursor and viewport scrolling."""
    focusable = True

    def __init__(self, value="", *, on_change=None, max_length=100_000, **kwargs):
        super().__init__(**kwargs)
        self.text, self.on_change, self.max_length = clean_text(value), on_change, max_length
        self.cursor, self.scroll_y, self.scroll_x = len(graphemes(self.text)), 0, 0

    def _position(self):
        before = "".join(graphemes(self.text)[:self.cursor])
        return before.count("\n"), len(graphemes(before.split("\n")[-1]))

    def paint(self, canvas):
        style = self.style or self.theme.panel
        canvas.fill(self.rect, style)
        row, col = self._position()
        lines = self.text.split("\n")
        col_px = text_width("".join(graphemes(lines[row])[:col]))
        self.scroll_y = max(0, min(self.scroll_y, row))
        if row >= self.scroll_y + self.rect.height:
            self.scroll_y = row - self.rect.height + 1
        self.scroll_x = max(0, min(self.scroll_x, col_px))
        if col_px >= self.scroll_x + self.rect.width:
            self.scroll_x = col_px - self.rect.width + 1
        for y, line in enumerate(lines[self.scroll_y:self.scroll_y + self.rect.height]):
            x = self.rect.x - self.scroll_x
            canvas.text(x, self.rect.y + y, line, style, self.rect.width + self.scroll_x)
        if self.focused:
            chars = graphemes(lines[row])
            canvas.put(self.rect.x + col_px - self.scroll_x, self.rect.y + row - self.scroll_y,
                       chars[col] if col < len(chars) else " ", style.with_(reverse=True))

    def on_event(self, event):
        if isinstance(event, Key) and event.phase == "release":
            return False
        chars = list(graphemes(self.text))
        insert = event.text if isinstance(event, Paste) else None
        changed = False
        if isinstance(event, Key):
            row, col = self._position()
            lines = self.text.split("\n")
            if event.name in ("up", "down"):
                target = max(0, min(len(lines) - 1, row + (1 if event.name == "down" else -1)))
                self.cursor = sum(len(graphemes(line)) + 1 for line in lines[:target]) + min(
                    col, len(graphemes(lines[target])))
            elif event.name == "left":
                self.cursor = max(0, self.cursor - 1)
            elif event.name == "right":
                self.cursor = min(len(chars), self.cursor + 1)
            elif event.name == "home":
                self.cursor -= col
            elif event.name == "end":
                self.cursor += len(graphemes(lines[row])) - col
            elif event.name in ("backspace", "delete"):
                i = self.cursor - 1 if event.name == "backspace" else self.cursor
                if 0 <= i < len(chars):
                    del chars[i]
                    self.cursor -= int(event.name == "backspace")
                    changed = True
            elif event.name == "enter":
                insert = "\n"
            elif event.text:
                insert = event.text
            else:
                return False
        if insert is not None:
            added = list(graphemes(clean_text(insert)))[:max(0, self.max_length - len(chars))]
            chars[self.cursor:self.cursor] = added
            self.cursor += len(added)
            changed = True
        if changed:
            self.text = "".join(chars)
            if self.on_change:
                self.on_change(self.text)
        self.invalidate()
        return isinstance(event, (Key, Paste))


class Slider(Widget):
    focusable = True

    def __init__(self, value=0.0, *, minimum=0.0, maximum=100.0, step=1.0, on_change=None, **kwargs):
        super().__init__(**kwargs)
        if maximum <= minimum or step <= 0:
            raise ValueError("maximum must exceed minimum and step must be positive")
        self.minimum, self.maximum, self.step, self.on_change = minimum, maximum, step, on_change
        self.value_ = min(max(value, minimum), maximum)
        self.motion = self.value(self.value_)

    def set_value(self, value):
        self.value_ = min(max(value, self.minimum), self.maximum)
        self.motion.set(self.value_)
        if self.on_change:
            self.on_change(self.value_)
        self.invalidate()

    def paint(self, canvas):
        r = self.rect
        n = max(0, r.width - 9)
        style = self.theme.selection if self.focused else self.theme.accent
        canvas.text(r.x, r.y, "─" * n, self.theme.muted)
        if n:
            p = (self.motion.current - self.minimum) / (self.maximum - self.minimum)
            canvas.put(r.x + min(n - 1, max(0, round(p * (n - 1)))), r.y, "●", style)
        canvas.text(r.x + n + 1, r.y, f"{self.value_:g}", style, max(0, r.width - n - 1))

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release":
            if event.name in ("left", "right", "home", "end"):
                value = self.minimum if event.name == "home" else self.maximum if event.name == "end" else (
                    self.value_ + self.step * (1 if event.name == "right" else -1))
                self.set_value(value)
                return True
        if isinstance(event, Mouse) and event.phase in ("press", "move"):
            n = max(1, self.rect.width - 10)
            p = min(1, max(0, (event.x - self.rect.x) / n))
            value = self.minimum + round(p * (self.maximum - self.minimum) / self.step) * self.step
            self.set_value(value)
            return True
        return False


class ProgressBar(Widget):
    def __init__(self, value=0.0, *, total=100.0, **kwargs):
        super().__init__(**kwargs)
        if total <= 0:
            raise ValueError("total must be positive")
        self.total = total
        self.progress = self.value(min(total, max(0, value)))

    def set_value(self, value):
        self.progress.set(min(self.total, max(0, value)))
        self.invalidate()

    def paint(self, canvas):
        r = self.rect
        width = max(0, r.width - 6)
        fraction = min(1, max(0, self.progress.current / self.total))
        subpixels = round(width * fraction * 8)
        full, partial = divmod(subpixels, 8)
        text = "█" * full + (" ▏▎▍▌▋▊▉"[partial] if partial else "")
        canvas.text(r.x, r.y, "░" * width, self.theme.muted)
        canvas.text(r.x, r.y, text, self.style or self.theme.accent, width)
        canvas.text(r.x + width + 1, r.y, f"{fraction:3.0%}", self.theme.muted, max(0, r.width - width - 1))


class Spinner(Widget):
    def __init__(self, label="Working", **kwargs):
        super().__init__(**kwargs)
        self.label, self.elapsed, self.running = label, 0.0, True

    def tick(self, dt):
        if self.running:
            old = int(self.elapsed * 10)
            self.elapsed += dt
            if int(self.elapsed * 10) != old:
                self.invalidate()

    def paint(self, canvas):
        glyph = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(self.elapsed * 10) % 10] if self.running else "✓"
        if self.app and self.app.animator.reduced_motion:
            glyph = "…" if self.running else "✓"
        canvas.text(self.rect.x, self.rect.y, glyph + " " + self.label,
                    self.style or self.theme.accent, self.rect.width)


class Sparkline(Widget):
    def __init__(self, values=(), **kwargs):
        super().__init__(**kwargs)
        self.samples = list(values)

    def paint(self, canvas):
        values = self.samples[-self.rect.width:] if self.rect.width else []
        if not values:
            return
        lo, hi = min(values), max(values)
        text = "".join("▁▂▃▄▅▆▇█"[round((v - lo) / (hi - lo or 1) * 7)] for v in values)
        canvas.text(self.rect.x, self.rect.y, text, self.style or self.theme.success)


class BarChart(Widget):
    def __init__(self, values: dict[str, float], **kwargs):
        super().__init__(**kwargs)
        self.samples = values

    def measure(self, width):
        return self.height if self.height is not None else len(self.samples)

    def paint(self, canvas):
        max_value = max(self.samples.values(), default=1) or 1
        label_width = min(14, max(0, self.rect.width // 3))
        n = max(0, self.rect.width - label_width - 8)
        for i, (label, value) in enumerate(list(self.samples.items())[:self.rect.height]):
            canvas.text(self.rect.x, self.rect.y + i, crop_text(label, label_width), self.theme.muted)
            canvas.text(self.rect.x + label_width + 1, self.rect.y + i,
                        "█" * max(0, round(n * value / max_value)), self.theme.accent, n)
            canvas.text(self.rect.x + label_width + n + 2, self.rect.y + i,
                        f"{value:g}", self.theme.background, 6)


class Log(Widget):
    focusable = True

    def __init__(self, *, max_lines=1000, **kwargs):
        super().__init__(**kwargs)
        if max_lines < 1:
            raise ValueError("max_lines must be positive")
        self.lines: list[str] = []
        self.max_lines, self.offset = max_lines, 0

    def write(self, text):
        self.lines.extend(clean_text(text).splitlines())
        self.lines = self.lines[-self.max_lines:]
        self.invalidate()

    def paint(self, canvas):
        end = max(0, len(self.lines) - self.offset)
        lines = self.lines[max(0, end - self.rect.height):end]
        for i, line in enumerate(lines):
            canvas.text(self.rect.x, self.rect.y + i, line, self.style or self.theme.muted, self.rect.width)

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("up", "down", "home", "end"):
            maximum = max(0, len(self.lines) - self.rect.height)
            self.offset = maximum if event.name == "home" else 0 if event.name == "end" else min(
                maximum, max(0, self.offset + (1 if event.name == "up" else -1)))
            self.invalidate()
            return True
        return False
