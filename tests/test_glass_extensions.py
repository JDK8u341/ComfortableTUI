"""Behavioral contracts for opt-in clocks and instance-safe control updates."""
from types import SimpleNamespace

import pytest

from comfortable_tui import GlassCard, GlassControl, GlassTheme, GlassWorkspace
from test_glass import Host


class PresentationHost(Host):
    def __init__(self):
        super().__init__()
        self.presentations = []

    def present(self, *args):
        self.presentations.append(args)


def workspace():
    control = GlassControl("status", "text", text="Readable ink")
    card = GlassCard("card", "Reusable card", [control])
    ws = GlassWorkspace([card])
    ws._native = PresentationHost()
    return ws, card, control


def test_frames_stop_without_scene_rebuild_and_use_real_time(monkeypatch):
    ws, card, control = workspace()
    clock, seen = iter((10., 10.037, 10.101)), []
    monkeypatch.setattr("comfortable_tui.glass.time.perf_counter", lambda: next(clock))
    def frame(f):
        seen.append(f)
        ws.present(card, control, opacity=.5)
        return f.index < 2
    request = ws.request_frames(frame, node=control)
    for _ in range(5):
        ws.poll()
    assert [f.index for f in seen] == [0, 1, 2]
    assert [f.dt for f in seen] == pytest.approx([0., .037, .064])
    assert seen[-1].elapsed == pytest.approx(.101)
    assert len(ws._native.scenes) == 1 and len(ws._native.presentations) == 3
    assert request.cancelled and not ws._frames
    assert control.opacity == .5
    ws.close()


def test_reduced_motion_calls_hook_once_even_if_it_requests_more():
    ws, _, control = workspace()
    ws.theme = GlassTheme(reduced_motion=True)
    seen = []
    ws.request_frames(lambda f: seen.append(f) or True, node=control)
    ws.poll()
    ws.poll()
    assert len(seen) == 1 and seen[0].reduced_motion and not ws._frames
    ws.close()


def test_hidden_frames_pause_and_resume_without_a_large_dt(monkeypatch):
    ws, _, control = workspace()
    clock, seen = iter((1., 10., 11.)), []
    monkeypatch.setattr("comfortable_tui.glass.time.perf_counter", lambda: next(clock))
    ws.request_frames(lambda f: seen.append(f) or True, node=control)
    ws.poll()
    ws.hide()
    ws.poll()
    assert len(seen) == 1
    ws.show()
    ws.poll()
    assert len(seen) == 2 and seen[-1].dt == 0 and seen[-1].elapsed == 10
    ws.close()


def test_replacement_drops_old_events_and_cancels_bound_frames():
    ws, card, old = workspace()
    seen = []
    ws.request_frames(lambda f: seen.append(f) or True, node=old)
    replacement = GlassControl(old.id, "text", text="New instance")
    card.controls = [replacement]
    ws.update()
    ws._native.inbox = [dict(kind="change", card=card.id, control=old.id,
                             value=.8, generation=old._generation)]
    ws.poll()
    assert not seen and replacement.value == 0 and not ws._frames
    with pytest.raises(ValueError, match="removed or replaced"):
        ws.present(card, old, opacity=.2)
    ws.close()


def test_native_hidden_card_pauses_only_its_bound_hook():
    ws, card, control = workspace()
    other = GlassCard("visible", "Visible")
    ws.set_cards([card, other])
    seen = []
    ws._native.metrics = lambda: dict(applied=0, error="", closed=False,
                                      windows=[dict(id=card.id, visible=False), dict(id=other.id, visible=True)])
    ws.request_frames(lambda f: seen.append("hidden") or True, node=control)
    ws.request_frames(lambda f: seen.append("visible"), node=other)
    ws.poll()
    assert seen == ["visible"]
    ws.close()


def test_callback_removal_is_seen_by_later_callbacks_in_the_same_poll():
    ws, card, control = workspace()
    seen = []
    ws.request_frames(lambda f: card.controls.clear())
    ws.request_frames(lambda f: seen.append(f), node=control)
    ws.poll()
    assert not seen and not ws._frames
    ws.close()


def test_new_requests_wait_for_next_poll_and_cancel_is_idempotent():
    ws, _, _ = workspace()
    seen = []
    def first(f):
        ws.request_frames(lambda f: seen.append("second"))
    ws.request_frames(first)
    request = ws.request_frames(lambda f: seen.append("cancelled"))
    request.cancel()
    request.cancel()
    ws.poll()
    assert not seen
    ws.poll()
    assert seen == ["second"]
    ws.close()


def test_hook_failure_and_close_release_scheduled_work():
    ws, _, _ = workspace()
    def fail(f):
        raise LookupError("callback failed")
    ws.request_frames(fail)
    with pytest.raises(LookupError, match="callback failed"):
        ws.poll()
    assert not ws._frames
    later = ws.request_frames(lambda f: True)
    ws.close()
    assert later.cancelled and not ws._frames
    with pytest.raises(RuntimeError, match="closed"):
        ws.request_frames(lambda f: True)


def test_opacity_rejects_nonfinite_values_and_does_not_fade_only_canvas_label():
    ws, card, control = workspace()
    for opacity in (-1, 2, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ws.present(card, control, opacity=opacity)
    control.kind = "canvas"
    with pytest.raises(ValueError, match="compositor"):
        ws.present(card, control, opacity=.3)
    ws.close()


def test_stale_card_selection_does_not_change_current_selection():
    ws, card, _ = workspace()
    other = GlassCard("other", "Other")
    ws.set_cards([card, other])
    ws._native.inbox = [dict(kind="selected", card=other.id, generation=other._generation+100)]
    ws.poll()
    assert ws.selected == card.id
    ws.close()


def test_requests_reject_noncallbacks_and_unowned_nodes():
    ws, _, _ = workspace()
    with pytest.raises(TypeError):
        ws.request_frames(None)
    with pytest.raises(ValueError):
        ws.request_frames(lambda f: True, node=SimpleNamespace())
    ws.close()
