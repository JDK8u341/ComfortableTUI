from types import SimpleNamespace

import pytest

from comfortable_tui import (App, FrameBuffer, GlassBackground, GlassCard, GlassCardGroup,
                             GlassControl, GlassTheme, GlassWorkspace, Label)


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
        self.drags = []

    def begin_drag(self, card, sequence):
        self.drags.append((card, sequence))

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
    assert workspace.metrics()["capture_active"] is False


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


def test_per_card_background_and_drag_policy_are_independent():
    clear = GlassCard("clear", "Clear", background=GlassBackground(opacity=.02), draggable=True)
    frost = GlassCard("frost", "Frosted", background=GlassBackground("frosted", blur=20), draggable=False)
    workspace = GlassWorkspace([clear, frost])
    clear.background.refraction = 30
    snapshot = workspace.snapshot()["cards"]
    assert snapshot[0]["background"]["kind"] == "liquid"
    assert snapshot[1]["background"]["blur"] == 20 and not snapshot[1]["draggable"]
    for values in (dict(blur=-1), dict(opacity=2), dict(refraction=float("inf")), dict(kind="dim")):
        with pytest.raises(ValueError):
            GlassBackground(**values).snapshot()


def test_groups_have_bounded_trees_and_explicit_layout_metadata():
    card = GlassCard("leaf", "Card")
    inner = GlassCardGroup("inner", [card], layout="column", enabled=False)
    outer = GlassCardGroup("outer", [inner], visible=False)
    workspace = GlassWorkspace([outer])
    assert workspace.cards == [card] and workspace.selected == "leaf"
    snapshot = workspace.snapshot()
    assert snapshot["roots"] == ["outer"] and snapshot["cards"][0]["group"] == "inner"
    assert snapshot["groups"][1]["parent"] == "outer" and not snapshot["groups"][1]["enabled"]
    inner.children.append(outer)
    with pytest.raises(ValueError, match="cycles"):
        workspace.update()
    inner.children.pop()
    with pytest.raises(ValueError):
        workspace.set_cards([outer, card])
    assert workspace.children == [outer]


def test_card_owns_drag_default_after_preview_and_bubbling():
    order = []
    card = GlassCard("card", "Card", on_event=lambda e: order.append("card"))
    inner = GlassCardGroup("inner", [card], on_preview_event=lambda e: order.append("inner preview"),
                          on_event=lambda e: order.append("inner bubble"))
    outer = GlassCardGroup("outer", [inner], on_preview_event=lambda e: order.append("outer preview"),
                          on_event=lambda e: order.append("outer bubble"))
    workspace = GlassWorkspace([outer], on_event=lambda e: order.append("workspace"))
    host = workspace._native = Host()
    down = dict(kind="pointer_down", card="card", text="header", sequence=9)
    host.inbox = [down]
    workspace.poll()
    assert order == ["outer preview", "inner preview", "card", "inner bubble", "outer bubble", "workspace"]
    assert host.drags == [("card", 9)]
    inner.on_preview_event = lambda e: True
    host.inbox = [down]
    workspace.poll()
    assert len(host.drags) == 1
    inner.on_preview_event = None
    card.draggable = False
    host.inbox = [down]
    workspace.poll()
    assert len(host.drags) == 1
    card.draggable = True
    card.on_event = lambda e: True
    host.inbox = [down]
    workspace.poll()
    assert len(host.drags) == 1
    card.on_event = None
    host.inbox = [dict(down, control="input")]
    workspace.poll()
    assert len(host.drags) == 1
    workspace.close()
