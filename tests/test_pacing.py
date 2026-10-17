"""Deadline accuracy and cached text equivalence under real terminal semantics."""
import math
import os
import random
from types import SimpleNamespace

import pytest
from wcwidth import wcswidth

from comfortable_tui import App, Canvas, Label, Rect, Style
from comfortable_tui._pacing import FramePacer
from comfortable_tui.canvas import clean_text, graphemes


def test_deadlines_do_not_accumulate_small_wakeup_delays_or_replay_stalls():
    pacer = FramePacer(0)
    for i in range(1, 61):
        deadline = pacer.advance((i - 1) / 60 + .002, 60)
        assert deadline == pytest.approx(i / 60)
    assert pacer.advance(3, 60) == 3
    assert pacer.advance(3.001, 60) == pytest.approx(3 + 1 / 60)


@pytest.mark.parametrize("fps", [60, 120])
def test_run_uses_high_resolution_clock_even_when_monotonic_is_coarse(monkeypatch, fps):
    import comfortable_tui.app as module
    now = [0.]
    fake_time = SimpleNamespace(perf_counter=lambda: now[0],
        monotonic=lambda: math.floor(now[0] * 64) / 64)
    monkeypatch.setattr(module, "time", fake_time)

    class Terminal:
        size = (20, 4)

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, text):
            now[0] += .001

        def read(self, timeout):
            now[0] += timeout
            return []

        def stop_input(self):
            pass

    monkeypatch.setattr(module, "Terminal", Terminal)
    with App(Label("state"), fps=fps) as app:
        timings = []
        app.on_frame = timings.append
        app.call_later(.12, app.exit)
        app.run()
        assert len(timings) >= fps * .1
        assert all(t.interval == pytest.approx(1 / fps) for t in timings[1:])
        assert all(t.write >= 0 and t.tick >= 0 for t in timings)


@pytest.mark.skipif(os.name != "nt", reason="Windows input deadline")
def test_windows_input_wait_is_not_quantized_to_gettickcount(monkeypatch):
    import msvcrt
    import comfortable_tui.terminal as module
    now = [0.]

    def sleep(delay):
        now[0] += delay
        assert now[0] < .02, "input wait must respect its high-resolution deadline"

    monkeypatch.setattr(module, "time", SimpleNamespace(
        perf_counter=lambda: now[0], monotonic=lambda: 0, sleep=sleep))
    monkeypatch.setattr(msvcrt, "kbhit", lambda: False)
    terminal = module.Terminal()
    assert terminal.read(.008) == []
    assert now[0] == pytest.approx(.008)


def test_cached_runs_match_individual_glyph_drawing_with_random_clipping():
    rng = random.Random(42)
    glyphs = ["a", "中", "e\u0301", "👩‍💻", " ", "\n", "\x1b", "▁"]
    for _ in range(180):
        source = "".join(rng.choices(glyphs, k=12))
        style = Style(fg=(rng.randrange(256), 120, 220))
        fast, reference = Canvas(24, 3), Canvas(24, 3)
        fast.text(0, 1, "中a中a中a中a中")
        reference.cells = fast.cells.copy()
        clip = Rect(rng.randrange(9), 0, rng.randrange(5, 16), 3)
        x, limit = rng.randrange(-4, 8), rng.randrange(0, 22)
        with fast.clipped(clip), reference.clipped(clip):
            returned = fast.text(x, 1, source, style, limit)
            column = x
            for glyph in graphemes(clean_text(source)):
                width = max(0, wcswidth(glyph))
                if glyph == "\n" or column - x + width > limit:
                    break
                if width:
                    reference.put(column, 1, glyph, style)
                    column += width
            assert returned == column - x
        assert fast.cells == reference.cells
