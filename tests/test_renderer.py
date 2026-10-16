import random

import pyte
import pytest

from comfortable_tui import Canvas, Rect, Renderer, Style, text_width
from comfortable_tui.renderer import python_diff


def assert_matches(screen, canvas):
    for y in range(canvas.height):
        expected = "".join(c.text for c in canvas.cells[y * canvas.width:(y + 1) * canvas.width])
        assert screen.display[y] == expected


def test_replacing_wide_glyphs_and_mutating_same_frame():
    frame = Canvas(16, 3)
    renderer = Renderer(synchronized=True)
    screen = pyte.Screen(16, 3)
    stream = pyte.Stream(screen)
    frame.text(1, 1, "中文 test")
    stream.feed(renderer.encode(frame))
    assert_matches(screen, frame)
    frame.text(1, 1, "ok")
    frame.put(4, 1, "x")  # overwrite continuation of 文
    stream.feed(renderer.encode(frame))
    assert_matches(screen, frame)
    assert renderer.encode(frame) == ""
    assert renderer.stats.bytes == 0


def test_random_frame_differences_reconstruct_screen():
    rng = random.Random(15)
    renderer = Renderer()
    screen = pyte.Screen(40, 12)
    stream = pyte.Stream(screen)
    frame = Canvas(40, 12)
    for _ in range(120):
        for __ in range(8):
            frame.put(rng.randrange(40), rng.randrange(12), rng.choice(["a", "中", " ", "z", "文"]),
                      Style(fg=(rng.randrange(256), 100, 200)))
        stream.feed(renderer.encode(frame))
        assert_matches(screen, frame)


def test_clipping_and_emoji_graphemes():
    frame = Canvas(12, 2)
    frame.text(0, 0, "e\u0301👩‍💻中")
    assert text_width("e\u0301👩‍💻中") == 5
    assert frame.cells[1].text == "👩‍💻"
    assert frame.cells[2].width == 0
    with frame.clipped(Rect(4, 1, 2, 1)):
        frame.text(3, 1, "中文")
    assert frame.cells[12 + 4].text == " "
    assert frame.cells[12 + 5].text == " "


def test_user_text_cannot_inject_ansi():
    frame = Canvas(40, 1)
    frame.text(0, 0, "\x1b[31mtext\x07\x9b\x1b]0;title")
    assert "\x1b" not in frame.plain()
    assert "\x07" not in frame.plain()
    assert "\x9b" not in frame.plain()


def test_resize_forces_repaint_and_color_modes():
    r = Renderer(colors="none")
    r.encode(Canvas(2, 2))
    assert "\x1b[2J" in r.encode(Canvas(3, 3))
    assert ";2;" not in r.encode(Canvas(4, 4))
    assert ";5;" in Renderer(colors="256").encode(Canvas(2, 2))
    assert r.encode(Canvas(0, 0)) == ""


def test_native_matches_python():
    native = pytest.importorskip("comfortable_tui._native")
    rng = random.Random(5)
    for _ in range(50):
        a = [rng.randrange(3) for i in range(96)]
        b = [rng.randrange(3) for i in range(96)]
        assert native.diff_spans(a, b, 12) == python_diff(a, b, 12)
    with pytest.raises(ValueError):
        native.diff_spans([], [], 0)
