import multiprocessing as mp
import queue
import subprocess
import sys
from types import SimpleNamespace

import pytest

from comfortable_tui import (App, Button, CanvasEvent, CanvasTransform, FrameBuffer, Key,
                             OverlayCanvas, Panel, PixelRect, Rect, Tabs, TerminalHost, VBox)
from comfortable_tui._overlay_qt import _EventSink, _pixel_placement


def _produce_frames(info, done):
    try:
        with FrameBuffer.attach(info) as buffer:
            for i in range(1, 501):
                with buffer.write() as pixels:
                    pixels[:] = bytes([i % 256]) * len(pixels)
        done.send(None)
    except BaseException as exc:
        done.send(repr(exc))
    finally:
        done.close()


@pytest.mark.parametrize("pixel_format,bpp", [("rgba8", 4), ("bgra8", 4),
                                              ("bgra8_premultiplied", 4), ("rgb8", 3)])
def test_buffer_formats_stride_and_view_lifetime(pixel_format, bpp):
    with FrameBuffer(3, 2, pixel_format=pixel_format) as buffer:
        assert buffer.info.stride >= 3 * bpp and buffer.info.stride % 4 == 0
        with buffer.read() as empty:
            assert empty is None
        source = bytearray(range(buffer.info.frame_bytes))
        buffer.submit(source, timestamp=123.5)
        source[:] = bytes(len(source))
        with FrameBuffer.attach(buffer.info) as consumer:
            with consumer.read() as frame:
                assert frame.sequence == 1 and frame.timestamp == 123.5
                assert frame.pixels.readonly
                assert bytes(frame.pixels) == bytes(range(buffer.info.frame_bytes))
            with pytest.raises(ValueError):
                bytes(frame.pixels)


def test_failed_write_does_not_publish_and_pinned_frame_survives_newer_frames():
    with FrameBuffer(8, 4) as buffer:
        buffer.submit(bytes([17]) * buffer.info.frame_bytes)
        with pytest.raises(RuntimeError, match="producer"):
            with buffer.write() as pixels:
                pixels[:] = bytes([88]) * len(pixels)
                raise RuntimeError("producer")
        assert buffer.sequence == 1
        with buffer.read() as original:
            for i in range(100):
                buffer.submit(bytes([i]) * buffer.info.frame_bytes)
            assert set(original.pixels) == {17}
        with buffer.read() as newest:
            assert newest.sequence == 101 and set(newest.pixels) == {99}


def test_close_during_a_lease_is_rejected_without_corrupting_the_buffer():
    with FrameBuffer(2, 2) as buffer:
        with buffer.write() as pixels:
            with pytest.raises(BufferError, match="live lease"):
                buffer.close()
            pixels[:] = bytes([42]) * len(pixels)
        with buffer.read() as frame:
            with pytest.raises(BufferError):
                buffer.close()
            assert set(frame.pixels) == {42}
        assert buffer.sequence == 1


def test_spawned_producer_cannot_overwrite_a_pinned_frame():
    context = mp.get_context("spawn")
    receive, send = context.Pipe(False)
    with FrameBuffer(320, 80) as buffer:
        buffer.submit(bytes([251]) * buffer.info.frame_bytes)
        with buffer.read() as pinned:
            process = context.Process(target=_produce_frames, args=(buffer.info, send))
            process.start()
            send.close()
            try:
                assert receive.poll(15), "producer did not finish while consumer pinned a different slot"
                assert receive.recv() is None
                assert set(pinned.pixels) == {251}
            finally:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                receive.close()
            assert process.exitcode == 0
        with buffer.read() as latest:
            assert latest.sequence == 501 and set(latest.pixels) == {500 % 256}


@pytest.mark.parametrize("kwargs", [{"width": 0, "height": 2}, {"width": 3, "height": -1},
                                    {"width": 3, "height": 2, "stride": 8},
                                    {"width": 3, "height": 2, "stride": 13},
                                    {"width": 3, "height": 2, "pixel_format": "yuv"},
                                    {"width": 10000, "height": 10000, "max_bytes": 1000}])
def test_invalid_buffer_allocation_is_rejected(kwargs):
    with pytest.raises(ValueError):
        FrameBuffer(**kwargs)


def test_submit_validates_size_and_contiguity():
    with FrameBuffer(2, 2) as buffer:
        with pytest.raises(ValueError):
            buffer.submit(bytes(15))
        with pytest.raises(ValueError):
            buffer.submit(memoryview(bytearray(32))[::2])
        with pytest.raises(ValueError):
            with buffer.write(timestamp=float("nan")):
                pass
    with pytest.raises(RuntimeError, match="closed"):
        buffer.submit(bytes(16))


@pytest.mark.parametrize("fit,expected", [("contain", PixelRect(0, 25, 100, 50)),
                                        ("cover", PixelRect(-50, 0, 200, 100)),
                                        ("stretch", PixelRect(0, 0, 100, 100)),
                                        ("pixel", PixelRect(-50, 0, 200, 100))])
def test_scale_and_inverse_pointer_mapping(fit, expected):
    transform = CanvasTransform.calculate((200, 100), (100, 100), fit, dpi_scale=1.5)
    assert transform.destination == expected
    assert transform.source_at(50, 50) == (100, 50)
    assert transform.contains_source(50, 50)
    if fit == "contain":
        assert not transform.contains_source(50, 0)


def test_clipped_cell_to_physical_coordinates_keep_full_transform():
    full, clip = _pixel_placement((Rect(1, -2, 8, 6), Rect(1, 0, 8, 4)), (10, 10),
                                 PixelRect(-100, 20, 150, 200))
    assert full == PixelRect(-85, -20, 120, 120)
    assert clip == PixelRect(-85, 20, 120, 80)


def test_headless_render_has_placements_but_never_imports_qt_or_starts_gui():
    # Run on every CI platform without installing the overlay extra.
    with FrameBuffer(4, 4) as buffer:
        canvas = OverlayCanvas(buffer)
        with App(Panel(canvas, "Video")) as app:
            frame = app.render(40, 12)
            _, full, clip = frame.overlays[id(canvas)]
            assert full == clip and full == canvas.rect
            assert app._overlay_sessions == {} and canvas._session is None
    subprocess.run([sys.executable, "-c", "import comfortable_tui, sys; "
                    "assert not any(n.startswith('PySide6') for n in sys.modules)"], check=True)


def test_tabs_modal_toast_and_removal_control_native_placements():
    with FrameBuffer(4, 4) as buffer:
        canvas = OverlayCanvas(buffer)
        tabs = Tabs({"Video": VBox(canvas), "Other": Button("Other")})
        with App(tabs, reduced_motion=True) as app:
            assert app.render(40, 12).overlays
            app.notify("toast")
            frame = app.render(40, 12)
            assert frame.overlays[id(canvas)][2].y + frame.overlays[id(canvas)][2].height <= 11
            app.show_modal(Panel(Button("Close")))
            assert not app.render(40, 12).overlays
            app.close_modal()
            tabs.select(1)
            app.render(40, 12, dt=1)
            assert not app.render(40, 12, dt=1).overlays


def test_canvas_provider_and_global_navigation_get_one_key_each():
    observed = []
    with FrameBuffer(4, 4) as buffer:
        canvas = OverlayCanvas(buffer, on_event=lambda e: observed.append(e) or True)
        button = Button("next")
        with App(VBox(canvas, button)) as app:
            app.render()
            app.focus(canvas)
            canvas._native_event(CanvasEvent("key_down", {"name": "tab", "native": True}))
            assert app.focused is button
            assert len(observed) == 1
            app.focus(canvas)
            app.dispatch(Key("left"))
            assert observed[-1].kind == "key_down"
            assert app.focused is canvas


def test_input_queue_is_bounded_coalesces_moves_and_preserves_clicks():
    output = queue.Queue(maxsize=2)
    sink = _EventSink(output)
    for i in range(2000):
        sink.emit(CanvasEvent("mouse_move", {"x": i}))
    sink.emit(CanvasEvent("mouse_down"))
    sink.emit(CanvasEvent("mouse_up"))
    sink.flush()
    assert [output.get_nowait().kind, output.get_nowait().kind] == ["mouse_down", "mouse_up"]
    sink.flush()
    assert output.get_nowait().data["x"] == 1999


def test_host_and_transform_validation():
    with pytest.raises(ValueError):
        TerminalHost(hwnd=-1)
    with pytest.raises(ValueError):
        TerminalHost(viewport=PixelRect())
    with pytest.raises(ValueError):
        CanvasTransform.calculate((1, 1), (2, 2), "unknown")
    with pytest.raises(ValueError):
        CanvasTransform.calculate((1, 1), (2, 2), dpi_scale=0)
    assert CanvasTransform.calculate((1, 1), (0, 0)).source_at(0, 0) is None


def test_host_prefers_visible_terminal_over_pseudoconsole(monkeypatch):
    from comfortable_tui import _win32_host as module
    api = SimpleNamespace(
        user=SimpleNamespace(GetForegroundWindow=lambda: 1, IsWindow=lambda hwnd: True,
                             IsWindowVisible=lambda hwnd: True, GetAncestor=lambda hwnd, kind: hwnd),
        kernel=SimpleNamespace(GetConsoleWindow=lambda: 2),
        class_name=lambda hwnd: {1: "CASCADIA_HOSTING_WINDOW_CLASS", 2: "PseudoConsoleWindow"}[hwnd])
    monkeypatch.setattr(module, "Win32", lambda: api)
    assert module.resolve_host(TerminalHost()).hwnd == 1
    api.class_name = lambda hwnd: {1: "UnrelatedApp", 2: "PseudoConsoleWindow"}[hwnd]
    with pytest.raises(RuntimeError, match="Cannot identify"):
        module.resolve_host(TerminalHost())
    assert module.resolve_host(TerminalHost(hwnd=2)).hwnd == 2


def test_uia_tab_title_is_not_a_terminal_pane():
    from comfortable_tui._win32_host import _terminal_text_candidates
    title = SimpleNamespace(CurrentClassName="TextBlock", CurrentControlType=50020, CurrentIsOffscreen=False)
    terminal = SimpleNamespace(CurrentClassName="TermControl", CurrentControlType=50020, CurrentIsOffscreen=False)
    hidden = SimpleNamespace(CurrentClassName="TermControl", CurrentControlType=50020, CurrentIsOffscreen=True)
    assert _terminal_text_candidates([title, terminal, hidden]) == [terminal]


def test_stream_marker_binds_when_foreground_is_an_unrelated_application(monkeypatch):
    from comfortable_tui import _win32_host as module
    monkeypatch.setattr(module, "Win32", lambda: SimpleNamespace())
    matched, output = [], []
    monkeypatch.setattr(module, "_marker_host", lambda marker: matched.append(marker) or (123, 2, (42, 43)))
    terminal = SimpleNamespace(size=(80, 24), write=output.append)
    host = module.resolve_host(TerminalHost(), terminal=terminal)
    assert host.hwnd == 123 and host.pane_index == 2
    assert host._runtime_id == (42, 43)
    assert matched[0] in output[0] and "\x1b7" in output[0] and "\x1b8" in output[0]


def test_marker_failure_never_binds_to_an_arbitrary_foreground_terminal(monkeypatch):
    from comfortable_tui import _win32_host as module
    monkeypatch.setattr(module, "Win32", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "_marker_host", lambda marker: None)
    with pytest.raises(RuntimeError, match="Cannot bind this terminal output stream"):
        module.resolve_host(TerminalHost(), terminal=SimpleNamespace(size=(80, 24), write=lambda s: None))


def test_terminal_itself_is_visible_even_when_its_root_owner_is_hidden():
    from comfortable_tui._win32_host import HostTracker
    tracker = HostTracker.__new__(HostTracker)
    tracker.host = TerminalHost(hwnd=1)
    tracker.api = SimpleNamespace(user=SimpleNamespace(
        IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: True, IsIconic=lambda hwnd: False,
        GetForegroundWindow=lambda: 1, GetAncestor=lambda hwnd, flags: 999))
    assert tracker.visible(2)


@pytest.mark.parametrize("z_order,foreground,expected_move", [([1, 2, 3], 1, (2, 0)),
                                                            ([2, 3, 1], 2, (1, 2)),
                                                            ([2, 1, 3], 2, None),
                                                            ([3, 1, 2], 3, (2, 3)),
                                                            ([2, 3, 1], 3, (2, 3)),
                                                            ([3, 2, 1], 3, None)])
def test_owner_group_order_is_restored_without_global_topmost_or_activation(z_order, foreground, expected_move):
    from comfortable_tui._win32_host import Win32
    calls = []
    api = Win32.__new__(Win32)

    def get_window(hwnd, command):
        if command == 4:
            return 1 if hwnd == 2 else 0
        index = z_order.index(hwnd) + (-1 if command == 3 else 1)
        return z_order[index] if 0 <= index < len(z_order) else 0

    def position(hwnd, after, x, y, w, h, flags):
        calls.append((hwnd, after or 0, flags))
        z_order.remove(hwnd)
        z_order.insert(0 if not after else z_order.index(after) + 1, hwnd)
        return True

    api.user = SimpleNamespace(GetWindow=get_window, GetForegroundWindow=lambda: foreground,
                               GetAncestor=lambda hwnd, flags: 1 if hwnd in (1, 2) else hwnd,
                               IsWindowVisible=lambda hwnd: True, SetWindowPos=position)
    api.class_name = lambda hwnd: "terminal" if hwnd == 1 else "canvas" if hwnd == 2 else "other"
    api.keep_above_owner(2, 1)
    assert z_order.index(2) < z_order.index(1)
    if expected_move:
        assert calls[0][:2] == expected_move
    else:
        assert not calls
    assert all(after not in (-1, -2) and flags & 0x10 for _, after, flags in calls)
    if foreground == 3:
        assert z_order == [3, 2, 1]


@pytest.mark.parametrize("shown,minimized,cloaked,expected", [(True, False, False, True),
    (False, False, False, False), (True, True, False, False), (True, False, True, False)])
def test_background_terminal_visibility_does_not_require_foreground(shown, minimized, cloaked, expected):
    from comfortable_tui._win32_host import HostTracker
    tracker = HostTracker.__new__(HostTracker)
    tracker.host = TerminalHost(hwnd=1)
    tracker.api = SimpleNamespace(cloaked=lambda hwnd: cloaked, user=SimpleNamespace(
        IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: shown, IsIconic=lambda hwnd: minimized,
        GetForegroundWindow=lambda: 999))
    assert tracker.visible(2) is expected


def test_late_canvas_focus_and_keys_cannot_reclaim_current_tui_control():
    from comfortable_tui import App, CanvasEvent, HBox, Input
    with FrameBuffer(2, 2) as buffer:
        view, field = OverlayCanvas(buffer), Input()
        with App(HBox(view, field)) as app:
            app.render(40, 10)
            app.focus(field)
            view._native_event(CanvasEvent("focus_in"))
            assert app.focused is field
            view._native_event(CanvasEvent("key_down", {"name": "a", "text": "a"}))
            assert app.focused is field and field.text == "a"
