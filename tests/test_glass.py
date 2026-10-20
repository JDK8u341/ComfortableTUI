from types import SimpleNamespace

import pytest

from comfortable_tui import (App, FrameBuffer, GlassBackground, GlassCard, GlassCardGroup,
                             GlassControl, GlassTheme, GlassWorkspace, Label)


def test_hosted_canvas_keeps_standard_events_and_buffer_ack_lifetime():
    from comfortable_tui import GlassCanvas, GlassEvent, OverlayCanvas
    events = []
    with FrameBuffer(16, 8) as first, FrameBuffer(32, 16) as second:
        canvas = OverlayCanvas(first, on_event=events.append)
        view = GlassCanvas("canvas", canvas, height=96)
        workspace = GlassWorkspace([GlassCard("a", "A", [view])])
        host = workspace._native = Host()
        try:
            workspace.poll()
            assert canvas._surface_host is view and canvas.app is None
            assert events[-1].kind == "attaching"
            host.applied = host.serial
            workspace.poll()
            assert events[-1].kind == "buffer_attached"
            view.default_event(GlassEvent("transform", position=(200, 100), value=1.5), workspace)
            assert canvas.transform.viewport_size == (300, 150)
            view.default_event(GlassEvent("mouse_down", position=(4, 5), buttons=1), workspace)
            assert events[-1].data["source_position"] == (4, 5)
            view.default_event(GlassEvent("control_focus_in"), workspace)
            view.default_event(GlassEvent("control_focus_out"), workspace)
            assert [e.kind for e in events[-2:]] == ["mouse_up", "focus_out"]
            canvas.set_buffer(second)
            assert workspace._dirty and events[-1].kind == "attaching"
            workspace.poll()
            assert first in workspace._attached_refs
            host.applied = host.serial
            workspace.poll()
            assert first not in workspace._attached_refs and second in workspace._attached_refs
            assert events[-1].data["buffer"] == second.info
            workspace.set_cards([])
            workspace.poll()
            assert canvas._surface_host is view
            host.applied = host.serial
            workspace.poll()
            assert canvas._surface_host is None and events[-1].kind == "detached"
            assert not canvas._closed and not first._closed and not second._closed
        finally:
            workspace.close()
            canvas.close()


def test_hosted_canvas_enforces_one_host_and_preserves_callback_navigation():
    from comfortable_tui import GlassCanvas, GlassEvent, OverlayCanvas
    from types import SimpleNamespace
    with FrameBuffer(4, 4) as source:
        received, commands = [], []
        canvas = OverlayCanvas(source, on_event=lambda e: received.append(e) or e.kind == "key_down")
        view = GlassCanvas("canvas", canvas)
        workspace = GlassWorkspace([GlassCard("a", "A", [view])])
        workspace._native = Host()
        workspace.poll()
        try:
            with pytest.raises(RuntimeError, match="one display host"):
                GlassCanvas("other", canvas)._mount(workspace)
            with pytest.raises(RuntimeError, match="mounted in a card"):
                canvas.mount(SimpleNamespace())
            view.default_event(GlassEvent("key_down", text="39"),
                               SimpleNamespace(command=lambda *v: commands.append(v)))
            assert received[-1].data["name"] == "right" and not commands
            canvas.on_canvas_event = received.append
            view.default_event(GlassEvent("key_down", text="39"),
                               SimpleNamespace(command=lambda *v: commands.append(v)))
            assert commands == [("next",)]
            canvas.configure(fit="cover")
            assert view.snapshot()["fit"] == "cover"
        finally:
            workspace.close()
            canvas.close()


def test_canvas_replacement_in_attach_callback_is_not_lost_and_close_waits_for_detach():
    from comfortable_tui import GlassCanvas, OverlayCanvas
    events = []
    with FrameBuffer(4, 4) as first, FrameBuffer(8, 8) as second:
        def event(e):
            events.append(e)
            if e.kind == "attaching" and canvas.buffer is first:
                canvas.set_buffer(second)
        canvas = OverlayCanvas(first, on_event=event)
        view = GlassCanvas("canvas", canvas)
        workspace = GlassWorkspace([GlassCard("a", "A", [view])])
        host = workspace._native = Host()
        try:
            workspace.poll()
            assert workspace._dirty
            workspace.poll()
            host.applied = host.serial
            workspace.poll()
            assert events[-1].kind == "buffer_attached" and events[-1].data["buffer"] == second.info
            canvas.close()
            assert not any(e.kind == "detached" for e in events)
            workspace.set_cards([])
            workspace.poll()
            host.applied = host.serial
            workspace.poll()
            assert events[-1].kind == "detached" and not second._closed
        finally:
            workspace.close()


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


@pytest.mark.parametrize("consume_at", [None, "card", "preview", "bubble", "workspace"])
def test_single_card_close_is_routed_and_waits_for_native_detach(consume_at):
    from comfortable_tui import GlassCanvas, OverlayCanvas
    events, route = [], []
    def handler(name):
        return lambda e: route.append(name) or consume_at == name
    with FrameBuffer(4, 4) as source:
        canvas = OverlayCanvas(source, on_event=events.append)
        closing = GlassCard("closing", "Close", [GlassCanvas("canvas", canvas)], on_event=handler("card"))
        sibling = GlassCard("keep", "Keep")
        group = GlassCardGroup("group", [closing, sibling], on_preview_event=handler("preview"),
                               on_event=handler("bubble"))
        workspace = GlassWorkspace([group], on_event=handler("workspace"))
        host = workspace._native = Host()
        workspace.poll()
        host.applied = host.serial
        workspace.poll()
        host.inbox = [dict(kind="card_close_request", card="closing")]
        try:
            workspace.poll()
            assert workspace.visible and not workspace._closed and not host.closed
            assert not any(e.kind == "detached" for e in events)
            assert canvas._surface_host is not None and not source._closed
            if consume_at is None:
                assert workspace.children == [group] and group.children == [sibling]
                assert workspace.selected == "keep"
                assert route == ["preview", "card", "bubble", "workspace"]
                host.applied = host.serial
                workspace.poll()
                assert [e.kind for e in events].count("detached") == 1
                assert canvas._surface_host is None and not source._closed
            else:
                assert group.children == [closing, sibling] and workspace.selected == "closing"
                assert route[-1] == consume_at
        finally:
            workspace.close()
            canvas.close()


def test_remove_card_preserves_nested_groups_and_rolls_back_failed_update(monkeypatch):
    closing, keep = GlassCard("closing", "Close"), GlassCard("keep", "Keep")
    inner = GlassCardGroup("inner", [closing])
    outer = GlassCardGroup("outer", [inner, keep])
    workspace = GlassWorkspace([outer])
    original_children = inner.children
    def fail():
        raise ValueError("fixture validation failed")
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "update", fail)
        with pytest.raises(ValueError, match="fixture validation"):
            workspace.remove_card("closing")
    assert inner.children is original_children and inner.children == [closing]
    assert workspace.selected == "closing" and workspace.children == [outer]
    workspace.remove_card("closing")
    assert inner.children == [] and outer.children == [inner, keep] and workspace.selected == "keep"
    with pytest.raises(ValueError, match="unknown card"):
        workspace.remove_card("missing")
    workspace.remove_card("keep")
    assert not workspace.cards and workspace.selected == "" and not workspace._closed
    workspace.close()


def test_close_callback_can_remove_or_replace_the_card_without_double_removal():
    for replace in (False, True):
        original, keep = GlassCard("card", "Original"), GlassCard("keep", "Keep")
        replacement = GlassCard("card", "Replacement")
        workspace = GlassWorkspace([original, keep])
        host = workspace._native = Host()
        def on_close(event):
            if event.kind == "card_close_request":
                workspace.set_cards([replacement, keep] if replace else [keep])
        original.on_event = on_close
        host.inbox = [dict(kind="card_close_request", card="card")]
        workspace.poll()
        assert workspace.cards == ([replacement, keep] if replace else [keep])
        workspace.close()


def test_workspace_close_detaches_all_cards_but_preserves_caller_owned_sources():
    from comfortable_tui import GlassCanvas, OverlayCanvas
    events = [[], []]
    with FrameBuffer(4, 4) as first, FrameBuffer(4, 4) as second:
        canvases = [OverlayCanvas(source, on_event=events[i].append) for i, source in enumerate((first, second))]
        workspace = GlassWorkspace([GlassCard(str(i), str(i), [GlassCanvas("canvas", canvas)])
                                    for i, canvas in enumerate(canvases)])
        host = workspace._native = Host()
        workspace.poll()
        host.applied = host.serial
        workspace.poll()
        workspace.close()
        workspace.close()
        assert host.closed and workspace.metrics()["closed"] and not workspace.metrics()["capture_active"]
        assert not workspace._bound_controls and not workspace._pending and not workspace._attached_refs
        assert all([e.kind for e in items].count("detached") == 1 for items in events)
        assert not first._closed and not second._closed
        for canvas in canvases:
            canvas.close()


def test_close_stops_independent_tracker_before_native_join_and_detaches_afterward():
    from comfortable_tui import GlassCanvas, OverlayCanvas
    order = []
    class Tracker:
        def request_close(self):
            order.append("tracker stop")
        def close(self):
            assert host.closed
            order.append("tracker join")
    class ClosingHost(Host):
        def close(self):
            assert order == ["tracker stop"]
            assert canvas._surface_host is not None and not source._closed
            order.append("native joined")
            super().close()
    with FrameBuffer(4, 4) as source:
        canvas = OverlayCanvas(source, on_event=lambda e: order.append(e.kind) if e.kind == "detached" else None)
        workspace = GlassWorkspace([GlassCard("a", "A", [GlassCanvas("view", canvas)])])
        host = workspace._native = ClosingHost()
        workspace.poll()
        workspace._tracker = Tracker()
        workspace.close()
        workspace.close()
        assert order == ["tracker stop", "native joined", "tracker join", "detached"]
        assert not source._closed
        canvas.close()


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
        def request_close(self):
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
