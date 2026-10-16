"""OLED-like pixel motion with the same Value spring as the cell UI.

Auto-negotiates Kitty graphics; --backend braille forces portable output.
"""
import argparse

from comfortable_tui import App, Label, Panel, PixelCanvas, Value, VBox


def build(backend="auto"):
    spring = Value(8, speed=15, damping=0.65)
    last = 0.0

    def paint(surface, elapsed):
        nonlocal last
        spring.set(100 if int(elapsed / 1.5) % 2 else 12)
        spring.step(max(0, elapsed - last))
        last = elapsed
        surface.clear()
        for i in range(5):
            surface.rect(20, 5 + i * 11, 95, 7, (31, 42, 63))
        surface.rect(spring.current, 3, 12, 58, (255, 124, 184))
        surface.circle(64, 32, 8, (122, 225, 190))
    return App(VBox(Label("OLED motion · Kitty pixels / braille fallback · Ctrl+Q exits", height=1),
                    Panel(PixelCanvas(painter=paint, animate=True, backend=backend), "128×64")), fps=120)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["auto", "braille", "kitty"], default="auto")
    args = parser.parse_args()
    with build(args.backend) as app:
        app.run()
