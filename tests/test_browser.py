"""Upper-layer browser startup contract; these tests do not load CEF."""
from pathlib import Path
from concurrent.futures import Future

import pytest

from comfortable_tui import browser


class FakeProcess:
    calls = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.capabilities = {"startup_ui_suppressed": True, "security_protocol": 1}
        self.messages = []
        self.calls.append(self)

    def close(self):
        pass

    def request(self, op, **kwargs):
        return {}

    def send(self, op, **kwargs):
        self.messages.append((op, kwargs))
        future = Future()
        future.set_result({})
        return future

    def events(self):
        return []


@pytest.mark.parametrize("application_id", [None, "example.application"])
def test_fresh_roots_start_without_interactive_browser_onboarding(monkeypatch, tmp_path, application_id):
    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    options = {"data_root": tmp_path / "data"} if application_id else {}
    roots = []
    for _ in range(2):
        with browser.BrowserService(application_id, executable=executable, **options) as service:
            roots.append(service.root)
            command = service._rpc.command
            assert "--no-first-run" in command
            assert "--no-default-browser-check" in command
            assert "--force-first-run" not in command
            assert "--no-sandbox" not in command
            assert "--ctui-root=" + str(service.root) in command
            assert not (service.root / "First Run").exists()
    assert (roots[0] == roots[1]) == (application_id is not None)
    if application_id is None:
        assert all(not root.exists() for root in roots)


def test_application_roots_still_isolate_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    with browser.BrowserService("app.a", executable=executable, data_root=tmp_path) as a:
        with browser.BrowserService("app.b", executable=executable, data_root=tmp_path) as b:
            assert a.root != b.root
            assert Path(a.root).parent == Path(b.root).parent


def test_retired_frames_wait_for_consumer_ack_then_are_released(monkeypatch, tmp_path):
    from comfortable_tui import CanvasEvent, WebView

    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    with browser.BrowserService(executable=executable) as service:
        page = service.open("about:blank", width=8, height=8)
        view = WebView(page, on_event=lambda event: True)
        view._session = object()
        view._emit(CanvasEvent("buffer_attached", {"buffer": page.source.info}))
        old = page.source
        page.resize(12, 12)
        old._check_open()
        view._emit(CanvasEvent("buffer_attached", {"buffer": page.source.info}))
        with pytest.raises(RuntimeError, match="closed"):
            old._check_open()
        final = page.source
        page.close()
        final._check_open()  # still displayed, even though its producer has stopped
        view.close()
        final._check_open()  # closing a logical widget is not a native detach acknowledgement
        view._emit(CanvasEvent("detached"))
        with pytest.raises(RuntimeError, match="closed"):
            final._check_open()
        assert page.id not in service._pages


def test_hidden_pages_share_one_app_poll_and_unsubscribe_on_unmount(monkeypatch, tmp_path):
    from comfortable_tui import App, Label, Tabs, WebView

    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    with browser.BrowserService(executable=executable) as service:
        page = service.open("about:blank", width=8, height=8)
        first, second = WebView(page), WebView(page)
        tabs = Tabs({"Other": Label("Hidden browsers still poll"), "First": first, "Second": second})
        with App(tabs) as app:
            assert len(app._event_pollers) == 1
            service._rpc.events = lambda: [{"view": page.id, "event": "title", "data": {"title": "arrived"}}]
            app.render(40, 12)
            assert page.title == "arrived"
        assert not app._event_pollers
        assert not service._app_bindings


@pytest.fixture
def browser_service(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    with browser.BrowserService(executable=executable) as service:
        yield service



def test_webview_tab_escape_and_f6_preserve_tui_focus(browser_service):
    from comfortable_tui import App, Button, CanvasEvent, HBox, Key, Tabs, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    view, button = WebView(page), Button("Next")
    tabs = Tabs({"Web": HBox(view, button)})
    with App(tabs) as app:
        app.render(60, 16)
        app.focus(view)
        assert not view.native_focus
        app.dispatch(Key("enter"))
        assert view.interacting and view.native_focus
        view._native_event(CanvasEvent("key_down", {"name": "tab"}))
        assert app.focused is button and not view.interacting
        view._native_event(CanvasEvent("focus_in"))
        assert app.focused is button
        app.dispatch(Key("shift+tab"))
        assert app.focused is view and not view.interacting
        view.enter_interaction()
        view._native_event(CanvasEvent("key_down", {"name": "escape"}))
        assert app.focused is view and not view.interacting
        view.enter_interaction()
        view._native_event(CanvasEvent("key_down", {"name": "f6"}))
        assert app.focused is tabs and not view.interacting
        assert not any(op == "input" and data.get("kind") == "key_down" and
                       data.get("data", {}).get("key_code") in (9, 27) for op, data in browser_service._rpc.messages)


def test_webview_embedded_in_virtual_list_defers_arrows_until_interacting(browser_service):
    from comfortable_tui import App, CanvasEvent, Key, ListView, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    listing = ListView(range(30), item_height=3, item_widget=lambda i, value: WebView(page))
    with App(listing, reduced_motion=True) as app:
        app.render(40, 12)
        view = listing._row_widgets[1]
        app.focus(view)
        view._native_event(CanvasEvent("key_down", {"name": "down"}))
        assert listing.selected == 1 and app.focused is listing and not view.interacting
        view._native_event(CanvasEvent("key_down", {"name": "down"}))
        assert listing.selected == 2
        view._native_event(CanvasEvent("mouse_down", {"source_position": (1, 1), "inside_source": True}))
        assert view.interacting and app.focused is view
        selected = listing.selected
        view._native_event(CanvasEvent("key_down", {"name": "down"}))
        assert listing.selected == selected
        view._native_event(CanvasEvent("key_down", {"name": "escape"}))
        assert app.focused is listing and not view.interacting
        view.enter_interaction()
        app.dispatch(Key("tab"))
        assert app.focused is not view and not view.interacting


@pytest.mark.parametrize("container", ["list", "table"])
@pytest.mark.parametrize("keyboard_release", [False, True])
def test_enter_from_selected_virtual_row_starts_webview_interaction(browser_service, container, keyboard_release):
    from comfortable_tui import App, CanvasEvent, DataTable, HBox, Key, Label, ListView, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    activated = []
    options = dict(item_height=4, on_select=lambda *args: activated.append(args))
    if container == "list":
        parent = ListView(range(1000), item_widget=lambda i, text: HBox(Label(text, width=8), WebView(page)),
                          **options)
    else:
        parent = DataTable(["Item", "Page"], [(str(i), "") for i in range(1000)], column_widths=[8, 20],
                           cell_widget=lambda r, c, value: WebView(page) if c == 1 else None, **options)
    with App(parent, reduced_motion=True) as app:
        app.keyboard_release = keyboard_release
        app.render(40, 14)
        parent.select(400)
        for _ in range(2):
            app.render(40, 14, dt=1)
        app.focus(parent)
        view = next(w for w in parent._row_widgets[400].walk() if isinstance(w, WebView))
        browser_service._rpc.messages.clear()
        app.dispatch(Key("enter"))
        assert app.focused is view and view.interacting and view.native_focus
        app.dispatch(Key("enter", phase="release"))
        assert activated == []
        assert not any(op == "input" and data.get("kind") in ("key_down", "key_up")
                       for op, data in browser_service._rpc.messages)
        # Further Enter presses belong to the page, rather than reactivating
        # the row. Escape restores its original navigation and selection.
        app.dispatch(Key("enter"))
        app.dispatch(Key("enter", phase="release"))
        assert any(op == "input" and data.get("kind") == "key_down" and
                   data["data"]["key_code"] == 13 for op, data in browser_service._rpc.messages)
        view._native_event(CanvasEvent("key_down", {"name": "escape"}))
        assert app.focused is parent and not view.interacting and parent.selected == 400
        app.dispatch(Key("down"))
        assert parent.selected == 401
        app.render(40, 14, dt=1)
        app.dispatch(Key("enter"))
        next_view = next(w for w in parent._row_widgets[401].walk() if isinstance(w, WebView))
        assert app.focused is next_view and next_view.interacting


@pytest.mark.parametrize("container", ["list", "table"])
def test_enter_skips_unavailable_embedded_webviews(browser_service, container):
    from comfortable_tui import App, DataTable, HBox, Key, ListView, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    unavailable, available = WebView(page, disabled=True), WebView(page)
    if container == "list":
        parent = ListView(["row"], item_height=4, item_widget=lambda i, text: HBox(unavailable, available))
    else:
        parent = DataTable(["Disabled", "Page"], [("", "")], item_height=4,
                           cell_widget=lambda r, c, value: (unavailable, available)[c])
    with App(parent) as app:
        app.render(60, 12)
        app.focus(parent)
        app.dispatch(Key("enter"))
        assert app.focused is available and available.interacting and not unavailable.interacting


@pytest.mark.parametrize("container", ["table", "text"])
def test_webview_in_table_or_text_can_navigate_to_neighbor(browser_service, container):
    from comfortable_tui import App, Button, CanvasEvent, DataTable, HBox, Text, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    neighbor = Button("Next")
    if container == "table":
        parent = DataTable(["Page"], [("one",)], column_widths=[12], item_height=4,
                           cell_widget=lambda r, c, value: WebView(page))
    else:
        parent = Text("Page ", WebView(page, width=12, height=4))
    with App(HBox(parent, neighbor, gap=2)) as app:
        app.render(60, 12)
        view = next(w for w in parent.walk() if isinstance(w, WebView))
        app.focus(view)
        view._native_event(CanvasEvent("key_down", {"name": "right"}))
        assert app.focused is neighbor and not view.interacting
