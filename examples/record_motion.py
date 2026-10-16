"""Record actual cell frames as a GIF (pip install Pillow).

This deterministic headless illustration is not a terminal FPS measurement.
"""
import argparse
from pathlib import Path

from comfortable_tui import App, Button, Checkbox, Input, Label, ListView, Panel, Tabs, VBox


def record(output: Path, font_path: str | None = None):
    from PIL import Image, ImageDraw, ImageFont

    candidates = [font_path] if font_path else [
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]
    font_path = next((path for path in candidates if path and Path(path).is_file()), None)
    font = ImageFont.truetype(font_path, 16) if font_path else ImageFont.load_default()
    name = Input("ComfortableTUI", height=1)
    checkbox = Checkbox("Keep motion smooth", checked=True, height=1)
    button = Button("Continue", height=1)
    tracks = ListView([f"Track {i:02d} / continuous selection" for i in range(100)], item_height=2)
    tabs = Tabs({"Forms": Panel(VBox(Label("SHARED FOCUS / continuous edges", height=1),
                                     name, checkbox, button, gap=1), "Profile"),
                 "Tracks": Panel(tracks, "Virtual list"),
                 "About": Panel(Label("Continuous motion. Stable surfaces. Interruptible menus."), "About")})
    root = VBox(Label("ComfortableTUI / motion preview", height=1), tabs,
                Label("Headless cell rendering / 50 fps / not a terminal recording", height=1))
    frames = []
    with App(root, colors="truecolor") as app:
        app.render(80, 20, dt=1)
        for i in range(200):
            if i in (5, 35, 65):
                app.focus({5: name, 35: checkbox, 65: button}[i])
            if i == 95:
                tabs.select(1)
            if i == 125:
                app.focus(tracks)
                tracks.select(5)
            if i == 155:
                tabs.select(2)
            if i == 163:
                tabs.select(1)
            if i == 180:
                tabs.select(0)
            canvas = app.render(80, 20, dt=1 / 50)
            image = Image.new("RGB", (800, 400))
            draw = ImageDraw.Draw(image)
            for index, cell in enumerate(canvas.cells):
                y, x = divmod(index, canvas.width)
                fg, bg = cell.style.fg, cell.style.bg
                if cell.style.reverse:
                    fg, bg = bg, fg
                draw.rectangle((x * 10, y * 20, (x + 1) * 10 - 1, (y + 1) * 20 - 1), fill=bg)
                if cell.width:
                    draw.text((x * 10, y * 20 + 1), cell.text, font=font, fill=fg)
            frames.append(image)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=20, loop=0, optimize=True)
    frames[42].save(output.with_suffix(".png"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("motion.gif"))
    parser.add_argument("--font", help="Path to a monospace TrueType font")
    args = parser.parse_args()
    record(args.output, args.font)
