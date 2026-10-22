import pytest

from comfortable_tui import FrameBuffer, GlassCanvas, GlassCard, GlassContainer, GlassControl, GlassWorkspace
from test_glass import Host


def test_nested_snapshot_preserves_unique_local_ids_and_parent_order():
    one, two = GlassControl("one", "button"), GlassControl("two", "button", weight=2)
    row = GlassContainer("row", [one, two], layout="row", gap=12)
    root = GlassContainer("root", [row], padding=8)
    card = GlassCard("card", "Tree", [root])
    nodes = card.snapshot()["controls"]
    assert [n["id"] for n in nodes] == ["root", "row", "one", "two"]
    assert [n["parent"] for n in nodes] == ["", "root", "row", "row"]
    assert card.control_route(two) == (root, row)
    assert nodes[-1]["weight"] == 2
    root.children.append(root)
    with pytest.raises(ValueError, match="tree"):
        card.snapshot()


def test_nested_events_preview_then_leaf_then_bubble_without_rebuilding():
    seen = []
    leaf = GlassControl("leaf", "button", on_event=lambda e: seen.append("leaf"))
    inner = GlassContainer("inner", [leaf], on_preview_event=lambda e: seen.append("inner-preview"),
                           on_event=lambda e: seen.append("inner-bubble"))
    outer = GlassContainer("outer", [inner], on_preview_event=lambda e: seen.append("outer-preview"),
                           on_event=lambda e: seen.append("outer-bubble"))
    card = GlassCard("card", "Route", [outer], on_event=lambda e: seen.append("card"))
    ws = GlassWorkspace([card])
    host = ws._native = Host()
    ws.poll()
    host.inbox.append(dict(kind="click", card="card", control="leaf", generation=leaf._generation))
    ws.poll()
    assert seen == ["outer-preview", "inner-preview", "leaf", "inner-bubble", "outer-bubble", "card"]
    assert len(host.scenes) == 1
    ws.close()


def test_multiple_nested_canvases_keep_single_consumers_and_detach_ack():
    with FrameBuffer(8, 8) as first, FrameBuffer(8, 8) as second:
        a, b = GlassCanvas("a", first), GlassCanvas("b", second)
        row = GlassContainer("row", [a, b], layout="row")
        ws = GlassWorkspace([GlassCard("card", "Two sources", [row])])
        host = ws._native = Host()
        ws.poll()
        assert ws._pending[1] == [first, second]
        host.applied = 1
        ws.poll()
        row.children.remove(a)
        ws.update()
        ws.poll()
        assert a._workspace is ws
        host.applied = 2
        ws.poll()
        assert a._workspace is None and b._workspace is ws
        assert not first._closed
        ws.close()
        a.canvas.close()
        b.canvas.close()
