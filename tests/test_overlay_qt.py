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
from comfortable_tui._overlay_qt import _make_window_class  # noqa: E402


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
