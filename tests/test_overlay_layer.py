"""Shared-layer routing and embedded widget lifecycle regressions."""
import queue

from comfortable_tui import (App, CanvasEvent, DataTable, FrameBuffer, HBox, Label,
                             ListView, OverlayCanvas, Rect, Text)
from comfortable_tui._overlay_qt import _EventSink


def test_shared_memory_name_fits_darwin_limit():
    with FrameBuffer(1, 1) as buffer:
        assert len(buffer.info.name.encode()) + 1 <= 31


def test_state_coalescing_is_per_canvas():
    output = queue.Queue()
    sink = _EventSink(output)
    for index in range(10):
        for key in (11, 22):
            sink.emit(CanvasEvent("transform", {"canvas_id": key, "index": index}))
    sink.flush()
    events = [output.get_nowait(), output.get_nowait()]
    assert {e.data["canvas_id"] for e in events} == {11, 22}
    assert all(e.data["index"] == 9 for e in events)


def test_list_factory_virtualizes_and_disposes_offscreen_canvases():
    with FrameBuffer(2, 2) as buffer:
        created = []

        def row(index, text):
            canvas = OverlayCanvas(buffer)
            created.append(canvas)
            return HBox(Label(text, width=8), canvas)

        listing = ListView(range(10000), item_height=3, item_widget=row)
        with App(listing, reduced_motion=True) as app:
            frame = app.render(40, 8)
            assert len(frame.overlays) <= 4
            old = list(created)
            listing.select(9999)
            app.render(40, 8, dt=1)
            frame = app.render(40, 8, dt=1)
            assert len(listing._row_widgets) <= 4
            assert all(canvas._closed and canvas.app is None for canvas in old)
            assert all(clip.y >= 0 and clip.y + clip.height <= 8 for _, _, clip in frame.overlays.values())
            assert len(list(app.root.walk())) < 20


def test_table_cells_clip_to_body_and_horizontal_viewport():
    with FrameBuffer(2, 2) as buffer:
        table = DataTable(["Name", "Video"], [(str(i), "") for i in range(30)],
                          column_widths=[8, 16], item_height=4,
                          cell_widget=lambda r, c, value: OverlayCanvas(buffer) if c == 1 else None)
        with App(table, reduced_motion=True) as app:
            frame = app.render(22, 9)
            assert frame.overlays
            for _, rect, clip in frame.overlays.values():
                assert clip.y >= 2 and clip.y + clip.height <= 9
                assert clip.x + clip.width <= 20
            assert table.hit_test(13, 0) is table
            table.horizontal_offset = 12
            frame = app.render(22, 9)
            assert all(clip.x >= 2 for _, _, clip in frame.overlays.values())


def test_inline_text_wraps_widgets_and_preserves_full_geometry_for_scaling():
    with FrameBuffer(2, 2) as buffer:
        canvas = OverlayCanvas(buffer, width=8, height=3)
        text = Text("12345", canvas, "\nTail")
        assert text.measure(10) == 5
        with App(text) as app:
            frame = app.render(10, 3)
            _, full, clip = frame.overlays[id(canvas)]
            assert full == Rect(0, 1, 8, 3)
            assert clip == Rect(0, 1, 8, 2)


def test_closing_one_canvas_does_not_close_layer_or_other_canvases():
    with FrameBuffer(2, 2) as buffer:
        a, b = OverlayCanvas(buffer), OverlayCanvas(buffer)
        calls = []
        a._session = b._session = type("Layer", (), {"close": lambda s: calls.append("close")})()
        a.close()
        assert not calls and b._session is not None and not b._closed


def test_app_creates_only_one_layer_and_passes_complete_scene(monkeypatch):
    from comfortable_tui import overlay
    calls = []

    class Session:
        def __init__(self, widget):
            calls.append(self)
            self.widgets = {}

        def sync(self, placements, grid):
            self.widgets = {key: entry[0] for key, entry in placements.items()}
            self.scene = placements

        def poll(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(overlay, "_OverlaySession", Session)
    with FrameBuffer(2, 2) as buffer, App(HBox(OverlayCanvas(buffer), OverlayCanvas(buffer))) as app:
        app.render()
        app._sync_overlays()
        app.render()
        app._sync_overlays()
        assert len(calls) == 1 and len(calls[0].scene) == 2


def test_embedded_canvas_focus_keys_and_wheel_bubble_to_list():
    with FrameBuffer(2, 2) as buffer:
        listing = ListView(range(30), item_height=3, item_widget=lambda i, text: OverlayCanvas(buffer))
        with App(listing, reduced_motion=True) as app:
            app.render(40, 12)
            view = listing._row_widgets[1]
            view._native_event(CanvasEvent("mouse_down"))
            assert app.focused is view and listing.selected == 1
            view._native_event(CanvasEvent("key_down", {"name": "down"}))
            assert listing.selected == 2
            view._native_event(CanvasEvent("wheel", {"angle_delta": (0, -120)}))
            assert listing.selected == 5


def test_removal_waits_for_detached_ack_even_after_unmount():
    from types import SimpleNamespace
    from comfortable_tui import VBox
    from comfortable_tui.overlay import _OverlaySession
    observed = []
    with FrameBuffer(2, 2) as buffer:
        canvas = OverlayCanvas(buffer, on_event=observed.append)
        root = VBox(canvas)
        with App(root) as app:
            app.render()
            session = _OverlaySession.__new__(_OverlaySession)
            session.app, session.widgets, session.retired = app, {}, {}
            session.host_spec = canvas.host
            session._closed = session._failed = False
            session._last = session._pending = None
            session._commands, session._events = queue.Queue(), queue.Queue()
            session._process = SimpleNamespace(is_alive=lambda: True)
            session.sync(app._overlay_placements, (80, 24))
            root.remove(canvas)
            assert not any(e.kind == "detached" for e in observed)
            session.sync({}, (80, 24))
            assert canvas._event_token in session.retired
            session._events.put(CanvasEvent("detached", {"canvas_id": id(canvas), "canvas_token": canvas._event_token}))
            session.poll()
            assert [e.kind for e in observed] == ["detached"]
            assert not session.retired and canvas.app is None
