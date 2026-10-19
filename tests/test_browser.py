"""Upper-layer browser startup contract; these tests do not load CEF."""
from pathlib import Path
from concurrent.futures import Future

import pytest

from comfortable_tui import browser


class FakeProcess:
    calls = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.capabilities = {"startup_ui_suppressed": True, "security_protocol": 2}
        self.messages = []
        self.calls.append(self)

    def close(self):
        pass

    def request(self, op, **kwargs):
        self.messages.append((op, kwargs))
        return {"security": kwargs["security"]} if op == "open" else {}

    def send(self, op, **kwargs):
        self.messages.append((op, kwargs))
        future = Future()
        future.set_result({})
        return future

    def events(self):
        return []


@pytest.fixture
def gpu_browser_service(monkeypatch, tmp_path):
    from comfortable_tui.gpu import GpuFrameInfo, GpuFrameSource

    class GpuProcess(FakeProcess):
        counter = 0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.capabilities["gpu_texture_transport"] = 1

        def request(self, op, **kwargs):
            result = super().request(op, **kwargs)
            if op in ("open", "resize"):
                self.counter += 1
                info = kwargs["source"]
                result.update(frame_backend="native_gpu_d3d11", source=dict(
                    name="Local\\CTUI_GPU_" + f"{self.counter:032x}", width=info["width"], height=info["height"]))
            return result

    sizes = {}
    original = GpuProcess.request

    def request(self, op, **kwargs):
        result = original(self, op, **kwargs)
        if "source" in result:
            sizes[result["source"]["name"]] = result["source"]
        return result

    GpuProcess.request = request

    def attach(name):
        info = sizes[name]
        return GpuFrameSource(GpuFrameInfo(name, info["width"], info["height"], 1, 0))

    monkeypatch.setattr(browser, "NativeProcess", GpuProcess)
    monkeypatch.setattr(browser, "gpu_available", lambda: True)
    monkeypatch.setattr(GpuFrameSource, "attach", attach)
    executable = tmp_path / "ctui_browser"
    executable.touch()
    with browser.BrowserService(executable=executable) as service:
        yield service


def test_gpu_frames_use_descriptors_and_release_only_after_display_ack(gpu_browser_service, monkeypatch):
    from comfortable_tui import CanvasEvent, WebView

    def unexpected_cpu_allocation(*args, **kwargs):
        raise AssertionError("GPU mode must not allocate a CPU pixel buffer")

    monkeypatch.setattr(browser, "FrameBuffer", unexpected_cpu_allocation)
    page = gpu_browser_service.open("about:blank", width=16, height=12, render_mode="gpu")
    view = WebView(page)
    view._session = object()
    view._emit(CanvasEvent("attaching", {"buffer": page.source.info}))
    first = page.source
    page.resize(20, 14)
    second = page.source
    page.resize(24, 16)
    assert len(page._buffers) == 3
    assert not first._closed and not second._closed
    view._emit(CanvasEvent("buffer_attached", {"buffer": page.source.info}))
    assert first._closed and second._closed
    final = page.source
    page.close()
    view.close()
    assert not final._closed
    view._emit(CanvasEvent("detached"))
    assert final._closed
    releases = [data["name"] for op, data in gpu_browser_service._rpc.messages if op == "release_gpu_frame"]
    assert releases == [first.info.name, second.info.name, final.info.name]


def test_gpu_surface_budget_includes_retired_allocations(gpu_browser_service):
    from comfortable_tui import BrowserLimits, CanvasEvent, WebView

    gpu_browser_service.limits = BrowserLimits(max_frame_bytes=3000, max_total_frame_bytes=4500)
    page = gpu_browser_service.open("about:blank", width=10, height=10, render_mode="gpu")
    view = WebView(page)
    view._session = object()
    view._emit(CanvasEvent("attaching", {"buffer": page.source.info}))
    page.resize(10, 10)
    with pytest.raises(PermissionError, match="total buffer"):
        page.resize(10, 10)
    view._emit(CanvasEvent("buffer_attached", {"buffer": page.source.info}))
    page.resize(10, 10)
    view._emit(CanvasEvent("detached"))
    view.close()


def test_gpu_unavailable_never_silently_falls_back(browser_service, monkeypatch):
    monkeypatch.setattr(browser, "gpu_available", lambda: True)
    with pytest.raises(RuntimeError, match="GPU-capable CEF"):
        browser_service.open("about:blank", render_mode="gpu")
    assert not browser_service._pages
    with pytest.raises(ValueError, match="render_mode"):
        browser_service.open("about:blank", render_mode="auto")


def test_gpu_backend_must_be_confirmed_by_the_helper(browser_service, monkeypatch):
    monkeypatch.setattr(browser, "gpu_available", lambda: True)
    browser_service.capabilities["gpu_texture_transport"] = 1
    with pytest.raises(RuntimeError, match="negotiate strict GPU"):
        browser_service.open("about:blank", render_mode="gpu")
    assert not browser_service._pages
    operations = [op for op, fields in browser_service._rpc.messages]
    assert operations[-2:] == ["close", "release_gpu_page"]


def test_failed_gpu_resize_attachment_closes_page_and_releases_new_channel(gpu_browser_service, monkeypatch):
    from comfortable_tui import GpuFrameSource

    page = gpu_browser_service.open("about:blank", width=16, height=12, render_mode="gpu")
    old = page.source

    def unavailable(name):
        raise OSError("cannot attach new GPU channel")

    monkeypatch.setattr(GpuFrameSource, "attach", unavailable)
    with pytest.raises(OSError, match="cannot attach"):
        page.resize(20, 16)
    assert page._closed and old._closed
    releases = [fields["name"] for op, fields in gpu_browser_service._rpc.messages if op == "release_gpu_frame"]
    assert len(releases) == 2 and releases[0] == old.info.name


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
        view._emit(CanvasEvent("attaching"))
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
            polls = []
            def events():
                polls.append(True)
                return [{"view": page.id, "event": "title", "data": {"title": "arrived"}}]
            service._rpc.events = events
            app.render(40, 12)
            assert page.title == "arrived"
            assert len(polls) == 1
            tabs.select(1)
            app.render(40, 12)
            assert len(polls) == 2  # Visible WebViews must not poll again in tick().
        assert not app._event_pollers
        assert not service._app_bindings


def test_continuous_resizes_and_mirrors_keep_all_inflight_sources_until_latest_ack(browser_service):
    from comfortable_tui import CanvasEvent, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    first, second = WebView(page), WebView(page)
    for view in (first, second):
        view._session = object()
        view._emit(CanvasEvent("attaching", {"buffer": page.source.info}))
        view._emit(CanvasEvent("buffer_attached", {"buffer": page.source.info}))
    original = page.source
    first.set_buffer(original)
    assert first._awaiting_buffer is None  # No new helper ack is sent for an unchanged source.
    assert page._attachments[first._event_token] == original.info
    page.resize(12, 12)
    intermediate = page.source
    page.resize(16, 16)
    current = page.source
    first._emit(CanvasEvent("buffer_attached", {"buffer": intermediate.info}))
    for source in (original, intermediate, current):
        source._check_open()
    first._emit(CanvasEvent("buffer_attached", {"buffer": current.info}))
    original._check_open()  # The second mirror still has an unacknowledged replacement.
    second.close()
    original._check_open()  # A logical close is not a native detach.
    second._emit(CanvasEvent("detached"))
    for source in (original, intermediate):
        assert source._closed
    assert not current._closed
    first.close()
    first._emit(CanvasEvent("detached"))


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


class GlassHostStub:
    def __init__(self):
        self.serial = self.applied = 0
        self.inbox, self.scenes, self.commands = [], [], []
        self.closed = False

    def configure(self, scene):
        self.serial += 1
        self.scenes.append(scene)
        return self.serial

    def command(self, action, value=""):
        self.commands.append((action, value))

    def metrics(self):
        return dict(applied=self.applied, active=True, error="", windows=[
            dict(id=name, visible=True, enabled=True, focused_control="browser") for name in ("a", "b")])

    def events(self):
        result, self.inbox = self.inbox, []
        return result

    def close(self):
        self.closed = True


def _glass_page(page, **kwargs):
    from comfortable_tui import GlassCard, GlassWebView, GlassWorkspace
    view = GlassWebView("browser", page, auto_resize=False, **kwargs)
    workspace = GlassWorkspace([GlassCard("a", "A", [view], width=400)])
    host = workspace._native = GlassHostStub()
    workspace.poll()
    host.applied = host.serial
    workspace.poll()
    return workspace, view, host


@pytest.mark.parametrize("gpu", [False, True])
def test_glass_browser_retires_sources_only_after_latest_scene_ack(browser_service, gpu_browser_service, gpu):
    service = gpu_browser_service if gpu else browser_service
    page = service.open("about:blank", width=8, height=8, render_mode="gpu" if gpu else "cpu")
    workspace, view, host = _glass_page(page)
    try:
        original = page.source
        assert page._attachments[view._token] == original.info
        page.resize(12, 12)
        intermediate = page.source
        workspace.poll()
        older_scene = host.serial
        page.resize(16, 16)
        workspace.poll()
        host.applied = older_scene
        workspace.poll()
        for source in (original, intermediate, page.source):
            source._check_open()
        assert page._attachments[view._token] is None
        host.applied = host.serial
        workspace.poll()
        assert original._closed and intermediate._closed and not page.source._closed
        assert page._attachments[view._token] == page.source.info
        workspace.set_cards([])
        workspace.poll()
        assert view.canvas in page._widgets and page._attachments
        host.applied = host.serial
        workspace.poll()
        assert view.canvas not in page._widgets and not page._attachments
        assert not service._app_bindings
    finally:
        workspace.close()


def test_glass_browser_routes_input_and_keeps_exit_keys(browser_service):
    from comfortable_tui import GlassEvent
    page = browser_service.open("about:blank", width=8, height=8)
    workspace, view, host = _glass_page(page)
    def event(kind, **fields):
        view.default_event(GlassEvent(kind, card="a", control="browser", **fields), workspace)
    try:
        event("mouse_down", position=(2, 3), source_name="retired-browser-frame")
        assert not view.interacting
        event("key_down", text="39")
        assert host.commands[-1] == ("next", "") and not view.interacting
        event("key_down", text="13")
        assert view.interacting
        event("key_up", text="13")
        event("key_down", text="65", modifiers=("ctrl",), scan_code=30)
        event("text", text="中文")
        messages = browser_service._rpc.messages
        assert any(op == "input" and data["kind"] == "text" and data["data"]["commit"] == "中文" for op, data in messages)
        assert any(op == "input" and data["kind"] == "key_down" and data["data"]["modifiers"] == ["ctrl"] for op, data in messages)
        event("key_down", text="27")
        assert not view.interacting
        event("key_down", text="27")
        assert host.commands[-1] == ("focus_terminal", "")
        event("mouse_down", position=(2, 3), button="right", buttons=2)
        assert view.interacting and "right" in view._buttons
        event("control_focus_out")
        assert not view.interacting and not view._buttons
    finally:
        workspace.close()


def test_glass_browser_visibility_is_shared_with_terminal_views(browser_service):
    from comfortable_tui import WebView
    page = browser_service.open("about:blank", width=8, height=8)
    terminal_view = WebView(page)
    workspace, view, host = _glass_page(page)
    try:
        assert terminal_view._page_visible()
        workspace.hide()
        workspace.poll()
        assert not terminal_view._page_visible()
        workspace.show()
        workspace.poll()
        assert terminal_view._page_visible()
        workspace.close()
        assert not terminal_view._page_visible()
    finally:
        workspace.close()
        terminal_view.close()


def test_glass_browser_polling_is_deduplicated_and_focus_loss_cannot_be_consumed(browser_service):
    from comfortable_tui import GlassCard, GlassWebView, GlassWorkspace
    page = browser_service.open("about:blank", width=8, height=8)
    other = browser_service.open("about:blank", width=8, height=8)
    first, second = GlassWebView("browser", page, auto_resize=False), GlassWebView("browser", other, auto_resize=False)
    workspace = GlassWorkspace([GlassCard("a", "A", [first]), GlassCard("b", "B", [second])], on_event=lambda e: True)
    host = workspace._native = GlassHostStub()
    try:
        workspace.poll()
        assert len(workspace._event_sources) == 1 and browser_service._app_bindings[workspace][0] == 2
        first.enter_interaction()
        host.inbox = [dict(kind="control_focus_out", card="a", control="browser")]
        workspace.poll()
        assert not first.interacting
    finally:
        workspace.close()
    assert not page._attachments and not other._attachments and not workspace._event_sources


def test_glass_browser_page_close_keeps_displayed_frames_until_workspace_detaches(browser_service):
    page = browser_service.open("about:blank", width=8, height=8)
    workspace, view, host = _glass_page(page)
    source = page.source
    view.enter_interaction()
    page.close()
    workspace.poll()
    assert not view.enabled and not view.interacting and not source._closed
    workspace.close()
    assert source._closed and not page._widgets and not page._attachments


def test_glass_browser_mount_failure_cleans_page_claim_and_listeners(browser_service, monkeypatch):
    from comfortable_tui import GlassCard, GlassWebView, GlassWorkspace
    page = browser_service.open("about:blank", width=8, height=8)
    view = GlassWebView("browser", page)
    workspace = GlassWorkspace([GlassCard("a", "A", [view])])
    workspace._native = GlassHostStub()
    def failed_binding(_):
        raise RuntimeError("fixture binding failed")
    monkeypatch.setattr(browser_service, "_bind_app", failed_binding)
    try:
        with pytest.raises(RuntimeError, match="binding failed"):
            workspace.poll()
        assert view._workspace is None and not page._widgets and not page._attachments
    finally:
        workspace.close()


def test_existing_webview_can_be_hosted_as_an_ordinary_canvas_and_remounted(browser_service):
    from comfortable_tui import App, GlassCanvas, GlassCard, GlassEvent, GlassWorkspace, WebView
    page = browser_service.open("about:blank", width=8, height=8)
    canvas = WebView(page)
    view = GlassCanvas("browser", canvas, auto_resize=True)
    for _ in range(2):
        page.resize(12, 12)
        workspace = GlassWorkspace([GlassCard("a", "A", [view])])
        host = workspace._native = GlassHostStub()
        workspace.poll()
        host.applied = host.serial
        workspace.poll()
        view.default_event(GlassEvent("key_down", text="13"), workspace)
        assert canvas.interacting and len(page._listeners) == 1
        workspace.close()
        assert not canvas.interacting and not page._listeners and not page._attachments
        assert canvas._surface_host is None and not canvas._closed
    with App(canvas) as app:
        app.render(40, 12)
        assert canvas.app is app and canvas in page._widgets and len(page._listeners) == 1
