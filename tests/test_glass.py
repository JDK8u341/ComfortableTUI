from types import SimpleNamespace

import pytest

from comfortable_tui import App, FrameBuffer, GlassCard, GlassControl, GlassTheme, GlassWorkspace, Label


def test_glass_models_are_headless_and_bounded():
    workspace = GlassWorkspace([GlassCard("tools", "Tools", [GlassControl("task", "button", "Run")])])
    assert workspace.snapshot()["cards"][0]["controls"][0]["kind"] == "button"
    assert workspace._native is None
    for control in (GlassControl("x", "slider", value=1.01), GlassControl("x", "choice"),
                    GlassControl("x", "choice", options=("a",), value=.5),
                    GlassControl("x", "sparkline", series=(float("nan"),)),
                    GlassControl("x", "text", text="a\0b"), GlassControl("x", "text", text="字" * 3000)):
        with pytest.raises(ValueError):
            control.snapshot()
    for theme in (dict(material="fake-blur"), dict(accent=-1)):
        with pytest.raises(ValueError):
            GlassTheme(**theme)
    with pytest.raises(ValueError):
        GlassWorkspace([GlassCard(str(i), "Card") for i in range(13)])
    with pytest.raises(ValueError):
        GlassWorkspace([GlassCard("same", "A"), GlassCard("same", "B")])
    workspace.close()
    with pytest.raises(RuntimeError):
        workspace.update()


def test_canvas_channels_and_control_ids_cannot_alias():
    with FrameBuffer(2, 2) as source:
        card = GlassCard("a", "A", [GlassControl("canvas", "canvas", source=source)])
        assert card.snapshot()["controls"][0]["source"]["gpu"] is False
        with pytest.raises(ValueError, match="one glass card"):
            GlassWorkspace([card, GlassCard("b", "B", [GlassControl("view", "canvas", source=source)])])
        with pytest.raises(ValueError, match="one canvas"):
            GlassCard("a", "A", [GlassControl("x", "canvas", source=source),
                                   GlassControl("y", "canvas", source=source)]).snapshot()
        with pytest.raises(ValueError, match="unique"):
            GlassCard("a", "A", [GlassControl("x", "text"), GlassControl("x", "button")]).snapshot()


class Host:
    def __init__(self):
        self.applied, self.serial, self.closed = 0, 0, False
        self.inbox, self.scenes = [], []

    def configure(self, scene):
        self.scenes.append(scene)
        self.serial += 1
        return self.serial

    def metrics(self):
        return dict(applied=self.applied, error="", closed=False)

    def events(self):
        items, self.inbox = self.inbox, []
        return items

    def close(self):
        self.closed = True


def test_canvas_reference_lifetime_follows_replacement_acknowledgement():
    with FrameBuffer(2, 2) as old, FrameBuffer(2, 2) as new:
        workspace = GlassWorkspace([GlassCard("a", "A", [GlassControl("c", "canvas", source=old)])])
        host = workspace._native = Host()
        workspace.poll()
        host.applied = 1
        workspace.poll()
        assert workspace._attached_refs == [old]
        workspace.set_cards([GlassCard("a", "A", [GlassControl("c", "canvas", source=new)])])
        workspace.poll()
        assert workspace._attached_refs == [old] and workspace._pending[2] == [new]
        host.applied = 2
        workspace.poll()
        assert workspace._attached_refs == [new] and not workspace._pending
        workspace.close()
        assert host.closed and not workspace._attached_refs


def test_native_events_update_models_and_dispatch_on_poll_thread():
    seen = []
    control = GlassControl("note", "input", on_event=seen.append)
    workspace = GlassWorkspace([GlassCard("a", "A", [control]), GlassCard("b", "B")], on_event=seen.append)
    host = workspace._native = Host()
    host.inbox = [dict(kind="selected", card="b"), dict(kind="change", card="a", control="note", text="hello", sequence=5)]
    workspace.poll()
    assert workspace.selected == "b" and control.text == "hello" and len(seen) == 3
    new = GlassControl("note", "input", text="replacement")
    workspace.set_cards([GlassCard("a", "A", [new])])
    assert new._acknowledged == 5
    workspace.close()


def test_native_failure_is_reported_even_after_worker_closed():
    workspace = GlassWorkspace([GlassCard("a", "A")])
    host = workspace._native = Host()
    host.metrics = lambda: dict(error="device removed", closed=True)
    with pytest.raises(RuntimeError, match="device removed"):
        workspace.poll()
    workspace.close()


def test_invalid_configuration_is_transactional_and_app_owns_cleanup():
    workspace = GlassWorkspace([GlassCard("a", "A")])
    with pytest.raises(ValueError):
        workspace.configure(layout="tiling")
    assert workspace.layout == "orbit"
    with pytest.raises(ValueError):
        workspace.set_cards([GlassCard("b", "B"), GlassCard("b", "C")])
    assert workspace.cards[0].id == workspace.selected == "a"
    with App(Label("terminal")) as app:
        workspace.bind(app)
        app.render()
        assert workspace._native is None
        host = workspace._native = Host()
        workspace._metrics = SimpleNamespace()
    assert host.closed and workspace._closed and not app._glass_workspaces


def test_first_binding_discovers_host_without_an_existing_overlay(monkeypatch):
    from comfortable_tui import _win32_host
    calls = []
    class Tracker:
        def __init__(self, host, grid, *, visibility_only=False):
            self.grid = grid
            assert visibility_only
        def snapshot(self):
            return SimpleNamespace(available=True, error="")
        def close(self):
            pass
    monkeypatch.setattr(_win32_host, "resolve_host", lambda *a, **k: SimpleNamespace(hwnd=42))
    monkeypatch.setattr(_win32_host, "HostTracker", Tracker)
    workspace = GlassWorkspace([GlassCard("a", "A")])
    def start(hwnd):
        calls.append(hwnd)
        workspace._native = Host()
    monkeypatch.setattr(workspace, "start", start)
    with App(Label("terminal")) as app:
        workspace.bind(app)
        app._terminal = object()
        workspace.poll()
        assert calls == [42]
        workspace.poll()
        assert workspace._pane_visible and workspace._native.scenes
