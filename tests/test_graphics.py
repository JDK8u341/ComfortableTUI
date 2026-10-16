import base64
import re
import zlib

from comfortable_tui import App, KittyEncoder, Panel, PixelCanvas, Rect, Surface


def test_kitty_chunks_round_trip_and_unchanged_frame_is_empty():
    surface = Surface(128, 64)
    # Pseudo-random bytes prevent compression from hiding chunking mistakes.
    import random
    surface.pixels[:] = random.Random(5).randbytes(len(surface.pixels))
    encoder = KittyEncoder(42)
    rect = Rect(0, 0, 20, 10)
    encoded = encoder.encode(surface, rect)
    chunks = re.findall(r"\x1b_G([^;]+);([^\x1b]*)\x1b\\", encoded)
    assert len(chunks) > 1
    assert all(len(payload) <= 4096 for _, payload in chunks)
    assert "m=0" in chunks[-1][0]
    assert zlib.decompress(base64.b64decode("".join(p for _, p in chunks))) == surface.pixels
    assert encoder.encode(surface, rect) == ""
    assert "d=I,i=42" in encoder.delete()


def test_braille_fallback_and_pixel_placement_cleanup():
    widget = PixelCanvas()
    widget.surface.rect(10, 10, 30, 30, (255, 0, 0))
    with App(widget) as app:
        frame = app.render(32, 16)
        assert not frame.graphics
        assert any(0x2800 <= ord(c.text[0]) < 0x2900 for c in frame.cells if c.text)
        app.graphics_supported = True
        frame = app.render(32, 16)
        assert len(frame.graphics) == 1
        assert "a=T" in app.encode(frame)
        assert app.encode(app.render(32, 16)) == ""
        widget.visible = False
        assert "a=d" in app.encode(app.render(32, 16))


def test_modal_removes_background_image():
    with App(PixelCanvas(backend="kitty")) as app:
        app.encode(app.render(32, 16))
        from comfortable_tui import Label
        app.show_modal(Panel(Label("modal"), width=20, height=6))
        assert "a=d" in app.encode(app.render(32, 16))
