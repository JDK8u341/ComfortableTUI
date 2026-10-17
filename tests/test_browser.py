"""Upper-layer browser startup contract; these tests do not load CEF."""
from pathlib import Path
from concurrent.futures import Future

import pytest

from comfortable_tui import browser


class FakeProcess:
    calls = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.capabilities = {"startup_ui_suppressed": True}
        self.calls.append(self)

    def close(self):
        pass

    def request(self, op, **kwargs):
        return {}

    def send(self, op, **kwargs):
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
