"""Immutable terminal styles and semantic themes."""
from dataclasses import dataclass, replace

Color = tuple[int, int, int] | None


@dataclass(frozen=True)
class Style:
    fg: Color = (218, 225, 240)
    bg: Color = (17, 21, 34)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False

    def with_(self, **changes):
        return replace(self, **changes)

    def blend(self, other: "Style", amount: float):
        """Interpolate colors while retaining the original text attributes."""
        amount = min(1.0, max(0.0, amount))
        def mix(a, b):
            if a is None or b is None:
                return b if amount >= 1 else a
            return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
        return self.with_(fg=mix(self.fg, other.fg), bg=mix(self.bg, other.bg))

    def ansi(self, colors: str = "truecolor") -> str:
        codes = ["0"]
        for enabled, code in [(self.bold, "1"), (self.italic, "3"),
                              (self.underline, "4"), (self.reverse, "7")]:
            if enabled:
                codes.append(code)
        if colors != "none":
            for color, channel in [(self.fg, 38), (self.bg, 48)]:
                if color is None:
                    continue
                r, g, b = color
                if colors == "256":
                    index = 16 + 36 * round(r / 51) + 6 * round(g / 51) + round(b / 51)
                    codes.append(f"{channel};5;{index}")
                else:
                    codes.append(f"{channel};2;{r};{g};{b}")
        return "\x1b[" + ";".join(codes) + "m"


@dataclass(frozen=True)
class Theme:
    background: Style = Style()
    panel: Style = Style(bg=(24, 30, 46))
    accent: Style = Style(fg=(255, 124, 184), bg=(24, 30, 46), bold=True)
    muted: Style = Style(fg=(133, 148, 173))
    selection: Style = Style(fg=(255, 231, 244), bg=(76, 42, 73), bold=True)
    success: Style = Style(fg=(122, 225, 190))
    error: Style = Style(fg=(255, 113, 134))

    @classmethod
    def light(cls):
        return cls(Style((31, 40, 61), (245, 247, 252)),
                   Style((31, 40, 61), (230, 235, 245)),
                   Style((160, 35, 103), (230, 235, 245), bold=True),
                   Style((91, 108, 136), (245, 247, 252)),
                   Style((83, 26, 64), (240, 191, 219), bold=True),
                   Style((20, 116, 80), (245, 247, 252)),
                   Style((179, 31, 59), (245, 247, 252)))
