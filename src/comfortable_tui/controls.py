"""Basic display, form and feedback controls."""
from __future__ import annotations


from wcwidth import wcswidth

from .canvas import Rect, clean_text, crop_text, graphemes, text_width
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
                        self.content_style(canvas), self.rect.width - offset)


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
            canvas.text(self.rect.x, self.rect.y + row, line, self.content_style(canvas))


class Text(Widget):
    """Wrapping text mixed with fixed-cell-size widgets, including OverlayCanvas.

    Text("Video: ", OverlayCanvas(buffer, width=16, height=4), " caption")
    Widget runs are indivisible boxes; lines grow to the tallest box.
    """

    def __init__(self, *runs, **kwargs):
        super().__init__(**kwargs)
        self.runs = []
        for run in runs:
            if isinstance(run, Widget):
                if run.width is None or run.width < 1 or run.height is not None and run.height < 1:
                    raise ValueError("inline widgets require width >= 1 and positive height")
                self.add(run)
                self.runs.append(run)
            else:
                self.runs.append(str(run))

    def _flow(self, width):
        if width <= 0:
            return [], 0
        result, line = [], []
        x, y, height = 0, 0, 1

        def newline():
            nonlocal x, y, height, line
            result.extend((run, xx, y, w, h) for run, xx, w, h in line)
            y += height
            x, height, line = 0, 1, []

        for run in self.runs:
            if isinstance(run, Widget):
                if not run.visible:
                    continue
                w = min(width, run.width)
                h = run.height or run.measure(w)
                if x and x + w > width:
                    newline()
                line.append((run, x, w, h))
                x, height = x + w, max(height, h)
            else:
                for glyph in graphemes(clean_text(run)):
                    w = max(0, text_width(glyph))
                    if glyph == "\n":
                        newline()
                        continue
                    if x + w > width:
                        newline()
                    if w <= width:
                        line.append((glyph, x, w, 1))
                        x += w
        newline()
        return result, y

    def measure(self, width):
        return self.height if self.height is not None else self._flow(width)[1]

    def layout(self, rect):
        super().layout(rect)
        self._placements, _ = self._flow(rect.width)
        for run, x, y, width, height in self._placements:
            if isinstance(run, Widget):
                run.layout(Rect(rect.x + x, rect.y + y, width, height))

    def paint(self, canvas):
        for run, x, y, width, height in self._placements:
            if isinstance(run, str):
                canvas.text(self.rect.x + x, self.rect.y + y, run, self.content_style(canvas), width)

    def focusables(self):
        if self.visible and not self.disabled:
            for child in self.children:
                clip = child.rect.intersect(self.rect)
                if min(clip.width, clip.height) > 0:
                    yield from child.focusables()


class Rule(Widget):
    def paint(self, canvas):
        canvas.text(self.rect.x, self.rect.y, "─" * self.rect.width, self.content_style(canvas, self.theme.muted))


class Spacer(Widget):
    pass


class Button(Widget):
    focusable = True

    def __init__(self, label: str, on_press=None, **kwargs):
        super().__init__(**kwargs)
        self.label, self.on_press = label, on_press
        self.pressed = self.value(0, role="control")
        self.armed = False

    def activate(self):
        if self.disabled:
            return
        self.pressed.kick(0.7)
        if self.on_press:
            self.on_press()
        self.invalidate()

    def paint(self, canvas):
        style = self.content_style(canvas, self.theme.panel)
        if self.disabled:
            style = self.theme.muted.with_(bg=style.bg)
        canvas.fill(self.rect, style)
        text = f"[ {self.label} ]"
        x = self.rect.x + max(0, (self.rect.width - text_width(text)) // 2)
        canvas.text(x,
                    self.rect.y + max(0, (self.rect.height - 1) // 2), text, style, self.rect.width)
        self.paint_feedback(canvas)

    def paint_feedback(self, canvas):
        amount = min(.45, max(0, self.pressed.current) * .6)
        if amount and not self.disabled:
            canvas.tint(self.rect.x, self.rect.y, self.rect.width, self.rect.height,
                        self.theme.accent.with_(fg=self.theme.panel.bg, bg=self.theme.accent.fg), amount)

    def paint_focus(self, canvas):
        self.paint_feedback(canvas)

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
        self.checked_mix = self.value(1 if checked else 0, role="effects")

    def layout(self, rect):
        super().layout(rect)
        self.checked_mix.set(1 if self.checked else 0)

    def activate(self):
        if self.disabled:
            return
        self.checked = not self.checked
        self.checked_mix.set(1 if self.checked else 0)
        self.pressed.kick(0.5)
        if self.on_change:
            self.on_change(self.checked)
        self.invalidate()

    def paint(self, canvas):
        style = self.content_style(canvas)
        canvas.text(self.rect.x, self.rect.y, f"[{'x' if self.checked else ' '}] {self.label}", style,
                    self.rect.width)
        marker = self.theme.muted.blend(self.theme.success, self.checked_mix.current).with_(bg=style.bg)
        canvas.text(self.rect.x, self.rect.y, f"[{'x' if self.checked else ' '}]", marker, min(3, self.rect.width))
        self.paint_feedback(canvas)


class Switch(Checkbox):
    def __init__(self, label: str, checked=False, **kwargs):
        super().__init__(label, checked, **kwargs)
        self.position = self.value(1 if checked else 0, role="control")

    def layout(self, rect):
        super().layout(rect)
        self.position.set(1 if self.checked else 0)

    def activate(self):
        super().activate()
        self.position.set(1 if self.checked else 0)

    def paint(self, canvas):
        style = self.content_style(canvas)
        canvas.text(self.rect.x, self.rect.y, "[     ] " + self.label, style, self.rect.width)
        position = min(1.1, max(-.1, self.position.current)) * 4
        marker = self.theme.muted.blend(self.theme.success, self.checked_mix.current).with_(bg=style.bg)
        canvas.put(self.rect.x + 1 + round(position), self.rect.y, "●", marker)
        canvas.tint(self.rect.x + 1 + position, self.rect.y, 1, 1, self.theme.selection, .65)
        self.paint_feedback(canvas)


class Input(Widget):
    focusable = True

    def __init__(self, value="", *, placeholder="", password=False, max_length=4096,
                 validator=None, on_change=None, on_submit=None, **kwargs):
        super().__init__(**kwargs)
        if max_length < 0:
            raise ValueError("max_length must be nonnegative")
        self.text = "".join(graphemes(clean_text(value).replace("\n", " "))[:max_length])
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
        style = self.content_style(canvas, self.theme.panel)
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
        canvas.text(r.x + 1, r.y, text, style if self.text else self.theme.muted.with_(bg=style.bg), available)
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
        if max_length < 0:
            raise ValueError("max_length must be nonnegative")
        self.text = "".join(graphemes(clean_text(value))[:max_length])
        self.on_change, self.max_length = on_change, max_length
        self.cursor, self.scroll_y, self.scroll_x = len(graphemes(self.text)), 0, 0

    def _position(self):
        before = "".join(graphemes(self.text)[:self.cursor])
        return before.count("\n"), len(graphemes(before.split("\n")[-1]))

    def paint(self, canvas):
        style = self.content_style(canvas, self.theme.panel)
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
        self.motion = self.value(self.value_, role="control")
        self._dragging = False

    def set_value(self, value):
        self.value_ = min(max(value, self.minimum), self.maximum)
        self.motion.set(self.value_)
        if self.on_change:
            self.on_change(self.value_)
        self.invalidate()

    def paint(self, canvas):
        r = self.rect
        n = max(0, r.width - 9)
        style = self.content_style(canvas, self.theme.accent)
        canvas.text(r.x, r.y, "─" * n, self.theme.muted.with_(bg=style.bg))
        if n:
            p = (self.motion.current - self.minimum) / (self.maximum - self.minimum)
            position = min(n - 1, max(0, p * (n - 1)))
            canvas.tint(r.x, r.y, position, 1, self.theme.selection, .45)
            canvas.put(r.x + round(position), r.y, "●", style)
            canvas.tint(r.x + position, r.y, 1, 1, self.theme.selection, .7)
        canvas.text(r.x + n + 1, r.y, f"{self.value_:g}", style, max(0, r.width - n - 1))

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release":
            if event.name in ("left", "right", "home", "end"):
                value = self.minimum if event.name == "home" else self.maximum if event.name == "end" else (
                    self.value_ + self.step * (1 if event.name == "right" else -1))
                self.set_value(value)
                return True
        if isinstance(event, Mouse):
            if event.phase == "release":
                dragging, self._dragging = self._dragging, False
                return dragging
            left_button = event.button & 3 == 0 and not event.button & 64
            if event.phase == "press":
                self._dragging = left_button and self.rect.contains(event.x, event.y)
                if not self._dragging:
                    return False
            elif event.phase == "move":
                if not left_button:
                    self._dragging = False
                if not self._dragging:
                    return False
            else:
                return False
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
        self.progress = self.value(min(total, max(0, value)), role="data")

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
        style = self.content_style(canvas, self.theme.accent)
        canvas.text(r.x, r.y, "░" * width, self.theme.muted.with_(bg=style.bg))
        canvas.text(r.x, r.y, text, style, width)
        canvas.text(r.x + width + 1, r.y, f"{fraction:3.0%}", self.theme.muted.with_(bg=style.bg),
                    max(0, r.width - width - 1))


class Spinner(Widget):
    def __init__(self, label="Working", **kwargs):
        super().__init__(**kwargs)
        self.label, self.elapsed, self.running = label, 0.0, True

    def tick(self, dt):
        if self.running:
            if self.app and self.app.animator.reduced_motion:
                return
            old = int(self.elapsed * 10)
            self.elapsed += dt
            if int(self.elapsed * 10) != old:
                self.invalidate()

    def paint(self, canvas):
        glyph = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(self.elapsed * 10) % 10] if self.running else "✓"
        if self.app and self.app.animator.reduced_motion:
            glyph = "…" if self.running else "✓"
        canvas.text(self.rect.x, self.rect.y, glyph + " " + self.label,
                    self.content_style(canvas, self.theme.accent), self.rect.width)


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
        canvas.text(self.rect.x, self.rect.y, text, self.content_style(canvas, self.theme.success))


class BarChart(Widget):
    def __init__(self, values: dict[str, float], **kwargs):
        super().__init__(**kwargs)
        self.samples = values
        self._bars = {}

    def layout(self, rect):
        super().layout(rect)
        visible = dict(list(self.samples.items())[:max(0, rect.height)])
        for key in list(self._bars):
            if key not in visible:
                self.release_value(self._bars.pop(key))
        maximum = max(self.samples.values(), default=1) or 1
        for key, sample in visible.items():
            if key not in self._bars:
                self._bars[key] = self.value(role="data")
            self._bars[key].set(min(1, max(0, sample / maximum)))

    def measure(self, width):
        return self.height if self.height is not None else len(self.samples)

    def paint(self, canvas):
        label_width = min(14, max(0, self.rect.width // 3))
        n = max(0, self.rect.width - label_width - 8)
        for i, (label, value) in enumerate(list(self.samples.items())[:self.rect.height]):
            style = self.content_style(canvas)
            canvas.text(self.rect.x, self.rect.y + i, crop_text(label, label_width),
                        self.theme.muted.with_(bg=style.bg))
            fraction = min(1, max(0, self._bars[label].current))
            full, partial = divmod(round(n * fraction * 8), 8)
            text = "█" * full + (" ▏▎▍▌▋▊▉"[partial] if partial else "")
            canvas.text(self.rect.x + label_width + 1, self.rect.y + i,
                        text, self.theme.accent.with_(bg=style.bg), n)
            canvas.text(self.rect.x + label_width + n + 2, self.rect.y + i,
                        f"{value:g}", style, 6)


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
            canvas.text(self.rect.x, self.rect.y + i, line, self.content_style(canvas, self.theme.muted), self.rect.width)

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name in ("up", "down", "home", "end"):
            maximum = max(0, len(self.lines) - self.rect.height)
            self.offset = maximum if event.name == "home" else 0 if event.name == "end" else min(
                maximum, max(0, self.offset + (1 if event.name == "up" else -1)))
            self.invalidate()
            return True
        return False
