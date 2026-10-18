"""Optional Qt integration tests. No native desktop or terminal is required."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QTextCharFormat  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from comfortable_tui import FrameBuffer, PixelRect  # noqa: E402
from comfortable_tui._overlay_qt import _make_layer_class, _make_window_class  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(qt_app):
    observed = []
    with FrameBuffer(4, 2) as buffer:
        buffer.submit(bytes([255, 0, 0, 255]) * 8)
        window = _make_window_class()(buffer, observed.append)
        window.fit, window.smooth = "stretch", False
        window.place(PixelRect(0, 0, 200, 100), PixelRect(0, 0, 200, 100))
        window.show()
        qt_app.processEvents()
        yield window, observed
        window.hide()
        window.destroy()
        qt_app.processEvents()


def test_external_buffer_is_drawn_and_fresh_frames_replace_it(window):
    widget, events = window
    image = widget.grab().toImage()
    assert image.pixelColor(100, 50).getRgb() == (255, 0, 0, 255)
    widget.buffer.submit(bytes([0, 255, 0, 255]) * 8)
    image = widget.grab().toImage()
    assert image.pixelColor(100, 50).getRgb() == (0, 255, 0, 255)
    assert widget._sequence == 2


def test_clicks_in_a_clipped_window_map_to_full_source(window, qt_app):
    widget, events = window
    widget.place(PixelRect(100, 200, 200, 100), PixelRect(150, 200, 100, 100))
    qt_app.processEvents()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(25, 50))
    click = next(e for e in reversed(events) if e.kind == "mouse_down")
    assert click.data["position"] == (75, 50)
    assert click.data["source_position"] == (1.5, 1)
    assert click.data["screen_position"] == (175, 250)
    assert click.data["inside_source"]


def test_contain_letterbox_and_mouse_outside_source(window):
    widget, events = window
    widget.fit = "contain"
    widget.place(PixelRect(0, 0, 100, 100), PixelRect(0, 0, 100, 100))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(50, 5))
    click = next(e for e in reversed(events) if e.kind == "mouse_down")
    assert not click.data["inside_source"]
    assert click.data["source_position"][1] < 0


def test_tab_and_modifier_keys_are_delivered_before_qt_focus_traversal(window):
    widget, events = window
    for code, mods, expected in ((Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, "tab"),
                                 (Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier, "shift+tab"),
                                 (Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier, "ctrl+q")):
        event = QKeyEvent(QEvent.Type.KeyPress, code, mods)
        QApplication.sendEvent(widget, event)
        assert events[-1].kind == "key_down" and events[-1].data["name"] == expected


def test_ime_commit_preedit_and_cursor_query(window):
    widget, events = window
    widget.ime = {"cursor": (1, 1, 0.5, 1), "text": "a😀b", "position": 3, "anchor": 1}
    event = QInputMethodEvent("拼", [])
    event.setCommitString("你好", -1, 1)
    QApplication.sendEvent(widget, event)
    assert events[-1].kind == "ime"
    assert events[-1].data["commit"] == "你好" and events[-1].data["preedit"] == "拼"
    assert events[-1].data["replacement_start"] == -1
    assert widget.inputMethodQuery(Qt.InputMethodQuery.ImCurrentSelection) == "😀"
    rect = widget.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (50, 50, 25, 50)


def test_ime_format_attributes_are_serializable_without_gui_objects(window):
    import pickle
    widget, events = window
    format = QTextCharFormat()
    format.setFontUnderline(True)
    attribute = QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.TextFormat, 0, 1, format)
    QApplication.sendEvent(widget, QInputMethodEvent("拼", [attribute]))
    event = pickle.loads(pickle.dumps(events[-1]))
    assert event.data["attributes"][0]["value"]["underline"]
    assert event.data["attributes"][0]["value"]["foreground"] is None


def test_close_is_a_provider_request_and_does_not_destroy_window(window):
    widget, events = window
    assert not widget.close()
    assert widget.isVisible()
    assert events[-1].kind == "close_request"


def layer_state(buffers, placements=None):
    from comfortable_tui import Rect
    return {"grid": (40, 20), "items": [
        {"id": i, "buffer": buffer.info, "placement": placements[i] if placements else
         (Rect(2 + i * 18, 2, 16, 8), Rect(2 + i * 18, 2, 16, 8)),
         "fit": "stretch", "smooth": False, "input_mode": "capture", "enabled": True,
         "focused": False, "ime": {"cursor": (0, 0, 1, 1), "text": "", "position": 0, "anchor": 0},
         "fps": 60} for i, buffer in enumerate(buffers)]}


def test_translation_keeps_scene_child_geometry_and_input_mapping(qt_app, monkeypatch):
    observed = []
    with FrameBuffer(4, 2) as buffer:
        layer = _make_layer_class()(observed.append)
        try:
            state = layer_state([buffer, buffer])
            layer.apply(state, PixelRect(100, 200, 400, 200))
            layer.show()
            qt_app.processEvents()
            item = layer.items[0]
            geometry, transform = item.geometry(), item.transform
            # Even the native path must never configure/ack a scene for a move.
            layer.native_renderer = True
            monkeypatch.setattr(layer, "_configure_native", lambda state: pytest.fail("scene rebuilt during move"))
            assert layer.relocate(PixelRect(150, 170, 400, 200))
            assert item.geometry() == geometry
            assert item.transform == transform
            QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            click = next(e for e in reversed(observed) if e.kind == "mouse_down")
            assert click.data["source_position"] == (0.5, 0.5)
            assert click.data["screen_position"] == (190, 210)
            assert click.data["layer_rect"] == PixelRect(150, 170, 400, 200)
            assert not layer.relocate(PixelRect(150, 170, 500, 200))
            layer._placement_dpi *= 2
            assert not layer.relocate(PixelRect(150, 170, 400, 200))
        finally:
            layer.shutdown()
            qt_app.processEvents()


def test_layer_has_one_native_window_routes_input_and_clears_old_pixels(qt_app):
    from comfortable_tui import Rect
    observed = []
    with FrameBuffer(2, 2) as a, FrameBuffer(2, 2) as b:
        a.submit(bytes([255, 0, 0, 255]) * 4)
        b.submit(bytes([0, 255, 0, 255]) * 4)
        layer = _make_layer_class()(observed.append)
        try:
            state = layer_state([a, b])
            layer.apply(state, PixelRect(0, 0, 400, 200))
            layer.show()
            qt_app.processEvents()
            assert all(not item.isWindow() and not item.internalWinId() for item in layer.items.values())
            ready = [event for event in observed if event.kind == "ready"]
            assert len({event.data["hwnd"] for event in ready}) == 1
            image = layer.grab().toImage()
            assert image.pixelColor(30, 30).red() == 255
            assert image.pixelColor(210, 30).green() == 255
            assert image.pixelColor(5, 5).alpha() == 0
            for key, item in layer.items.items():
                QTest.mouseClick(item, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
                event = next(e for e in reversed(observed) if e.kind == "mouse_down")
                assert event.data["canvas_id"] == key
                assert event.data["source_position"] == (0.25, 0.5)
            state["items"][0]["placement"] = (Rect(2, 12, 16, 8), Rect(2, 12, 16, 8))
            state["items"][1]["placement"] = None
            layer.apply(state, PixelRect(0, 0, 400, 200))
            qt_app.processEvents()
            image = layer.grab().toImage()
            assert image.pixelColor(30, 30).alpha() == 0
            assert image.pixelColor(210, 30).alpha() == 0
            assert image.pixelColor(30, 130).red() == 255
            assert not layer.items[1].isVisible()
            state["items"] = state["items"][:1]
            layer.apply(state, PixelRect(0, 0, 400, 200))
            assert list(layer.items) == [0]
        finally:
            layer.shutdown()
            qt_app.processEvents()


def test_layer_clip_hit_order_keyboard_ime_and_passthrough(qt_app):
    from comfortable_tui import Rect
    observed = []
    with FrameBuffer(2, 2) as buffer:
        layer = _make_layer_class()(observed.append)
        try:
            placement = (Rect(2, 2, 16, 8), Rect(4, 4, 10, 6))
            state = layer_state([buffer, buffer], [placement, placement])
            layer.apply(state, PixelRect(100, 200, 400, 200))
            layer.show()
            qt_app.processEvents()
            top = layer.childAt(QPoint(60, 60))
            assert top is layer.items[1]
            assert layer.childAt(QPoint(30, 30)) is None
            QTest.mouseClick(top, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            click = next(e for e in reversed(observed) if e.kind == "mouse_down")
            assert click.data["source_position"] == (0.5, 1)
            QTest.keyClick(top, Qt.Key.Key_A)
            QApplication.sendEvent(top, QInputMethodEvent("拼", []))
            assert next(e for e in reversed(observed) if e.kind == "ime").data["canvas_id"] == 1
            state["items"][1]["input_mode"] = "passthrough"
            layer.apply(state, PixelRect(100, 200, 400, 200))
            assert layer.childAt(QPoint(60, 60)) is layer.items[0]
            # An opaque pass-through item over a TUI area routes to terminal cells.
            QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(390, 190))
            routed = [e for e in observed if e.kind == "tui_mouse"]
            assert [e.data["event"]["phase"] for e in routed] == ["press", "release"]
            assert routed[0].data["event"]["x"] == 39
        finally:
            layer.shutdown()
            qt_app.processEvents()


def test_layer_retirement_ack_follows_buffer_release_and_ids_can_be_reused(qt_app):
    observed = []
    with FrameBuffer(2, 2) as buffer:
        layer = _make_layer_class()(observed.append)
        try:
            state = layer_state([buffer])
            state["items"][0]["token"] = "old"
            layer.apply(state, PixelRect(0, 0, 400, 200))
            old = layer.items[0]
            state["items"][0]["token"] = "new"
            state["retired"] = [{"id": 0, "token": "old"}]
            layer.apply(state, PixelRect(0, 0, 400, 200))
            assert layer.items[0] is not old
            assert old.buffer._closed
            event = next(e for e in reversed(observed) if e.kind == "detached")
            assert event.data["canvas_token"] == "old"
            state["items"] = []
            state["retired"] = [{"id": 0, "token": "new"}]
            layer.apply(state, PixelRect(0, 0, 400, 200))
            assert not layer.items
            assert next(e for e in reversed(observed) if e.kind == "detached").data["canvas_token"] == "new"
        finally:
            layer.shutdown()
            qt_app.processEvents()


def test_surface_does_not_take_or_return_foreground_while_owner_is_background(window, qt_app):
    from types import SimpleNamespace
    widget, _ = window
    returned = []
    widget.owner = 1
    widget.api = SimpleNamespace(user=SimpleNamespace(GetForegroundWindow=lambda: 999,
        GetAncestor=lambda hwnd, flags: 999, SetForegroundWindow=returned.append))
    try:
        widget._want_focus = True
        widget.request_focus()
        assert widget._want_focus  # postponed, without activating the background group
        widget.activateWindow()
        qt_app.processEvents()
        widget.setFocus()
        qt_app.processEvents()
        state = layer_state([widget.buffer])["items"][0]
        widget.configure(state)
        assert not widget.hasFocus()
        assert not returned
    finally:
        widget.api = None


def test_layer_without_a_focused_surface_forwards_tui_navigation_and_text(qt_app):
    events = []
    layer = _make_layer_class()(events.append)
    try:
        for code, name in ((Qt.Key.Key_Tab, "tab"), (Qt.Key.Key_F6, "f6")):
            QApplication.sendEvent(layer, QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier))
            assert events[-1].kind == "tui_key" and events[-1].data["event"]["name"] == name
        text = QInputMethodEvent()
        text.setCommitString("你好")
        QApplication.sendEvent(layer, text)
        assert events[-1].kind == "tui_text" and events[-1].data["text"] == "你好"
    finally:
        layer.shutdown()
