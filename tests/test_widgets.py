import importlib.util
from pathlib import Path

import pytest

from comfortable_tui import (App, Button, Checkbox, DataTable, HBox, Input, Key,
                             ListView, Mouse, PageStack, Panel, Paste, Select, SelectionList, Slider, Tabs, TextArea, Tree, TreeNode)
from comfortable_tui.demo import gallery
from comfortable_tui import ScrollView


def settle(app, w=80, h=24):
    for _ in range(6):
        frame = app.render(w, h, dt=1)
    return frame


def test_input_edits_graphemes_and_validates():
    changes = []
    field = Input(on_change=changes.append, validator=lambda text: "Required" if not text else True)
    with App(field) as app:
        app.render(20, 1)
        app.dispatch(Paste("中👩‍💻e\u0301"))
        app.dispatch(Key("backspace"))
        assert field.text == "中👩‍💻"
        app.dispatch(Key("left"))
        app.dispatch(Key("delete"))
        assert field.text == "中"
        app.dispatch(Key("ctrl+u"))
        assert field.error == "Required"
        assert changes[-1] == ""


def test_password_snapshot_does_not_expose_secret():
    with App(Input("secret中", password=True)) as app:
        assert "secret" not in app.render(20, 1).plain()


def test_button_release_and_disabled_focus():
    clicks = []
    button = Button("Go", lambda: clicks.append(1))
    with App(HBox(button, Button("Disabled", disabled=True))) as app:
        app.render(40, 2)
        app.keyboard_release = True
        app.dispatch(Key("enter"))
        app.dispatch(Key("enter", phase="repeat"))
        assert clicks == []
        app.dispatch(Key("enter", phase="release"))
        assert clicks == [1]
        app.dispatch(Key("tab"))
        assert app.focused is button
        app.keyboard_release = False
        app.dispatch(Key("enter"))
        app.dispatch(Key("enter", phase="release"))
        assert clicks == [1, 1]


def test_mouse_capture_cancels_outside_click():
    clicks = []
    a = Button("a", lambda: clicks.append("a"))
    b = Button("b", lambda: clicks.append("b"))
    with App(HBox(a, b)) as app:
        app.render(20, 2)
        app.dispatch(Mouse(2, 0))
        app.dispatch(Mouse(15, 0, phase="release"))
        assert clicks == []
        app.dispatch(Mouse(2, 0))
        app.dispatch(Mouse(2, 0, phase="release"))
        assert clicks == ["a"]


def test_multiline_editor_preserves_grapheme_positions():
    editor = TextArea("ab\n中文")
    with App(editor) as app:
        app.render(8, 3)
        app.dispatch(Key("up"))
        app.dispatch(Key("home"))
        app.dispatch(Paste("👩‍💻"))
        assert editor.text == "👩‍💻ab\n中文"
        app.dispatch(Key("enter"))
        assert editor.text.startswith("👩‍💻\nab")


def test_virtual_list_scrolls_and_mutations_release_animation_values():
    listing = ListView([str(i) for i in range(10_000)], item_height=2)
    with App(listing) as app:
        app.render(40, 10)
        baseline = len(app.animator.values)
        listing.select(9999)
        frame = settle(app, 40, 10)
        assert "9999" in frame.plain()
        assert listing.scroll.current == 19990
        listing.insert_item(2, "inserted")
        settle(app, 40, 10)
        listing.remove_item(2)
        settle(app, 40, 10)
        assert len(listing.items) == 10_000
        assert len(app.animator.values) == baseline


def test_tabs_only_focus_active_content_and_modal_restores_focus():
    first, second = Input("first"), Input("second")
    tabs = Tabs({"One": first, "Two": second})
    with App(tabs) as app:
        app.render(40, 10)
        app.focus(first)
        tabs.select(1)
        app.render(40, 10)
        assert first not in list(tabs.focusables())
        app.focus(second)
        app.show_modal(Panel(Button("Okay"), "Dialog", width=20, height=5))
        app.render(40, 10)
        assert app.focused is not second
        app.dispatch(Key("escape"))
        assert app.modal is None and app.focused is second


def test_page_pop_cleans_up_and_restores_focus():
    first = Input("state")
    pages = PageStack(first)
    with App(pages) as app:
        settle(app)
        app.focus(first)
        baseline = len(app.animator.values)
        pages.push(Button("new"))
        settle(app)
        assert len(pages.children) == 2
        assert pages.pop()
        settle(app)
        assert len(pages.children) == 1
        assert first.text == "state" and app.focused is first
        assert len(app.animator.values) == baseline
        assert not pages.pop()


def test_tree_expansion_and_table_selection():
    root = TreeNode("root", [TreeNode("child", [TreeNode("leaf")])], expanded=True)
    tree = Tree(root)
    tree.select(1)
    tree.on_event(Key("right"))
    assert len(tree.nodes) == 3
    tree.on_event(Key("left"))
    assert len(tree.nodes) == 2
    selected = []
    table = DataTable(["Name", "Value"], [("a", "1"), ("b", "2")], on_select=lambda i, row: selected.append(row))
    with App(table) as app:
        frame = app.render(40, 6)
        assert "Name" in frame.plain()
        app.dispatch(Key("down"))
        app.dispatch(Key("enter"))
        assert selected == [("b", "2")]


def test_select_popup_and_checkbox_slider():
    select = Select(["a", "b"])
    with App(select) as app:
        app.render(40, 8)
        app.dispatch(Key("enter"))
        app.render(40, 8)
        app.dispatch(Key("down"))
        app.dispatch(Key("enter"))
        assert select.selected == 1 and app.modal is None
    check = Checkbox("check")
    check.activate()
    assert check.checked
    slider = Slider(0, maximum=10)
    slider.on_event(Key("end"))
    assert slider.value_ == 10


def test_selection_list_indices_follow_insert_delete():
    listing = SelectionList(["a", "b", "c"], checked=[1, 2])
    listing.insert_item(1, "new", animate=False)
    assert listing.checked == {2, 3}
    listing.remove_item(2, animate=False)
    assert listing.checked == {2}


def test_timers_and_queued_callbacks_are_deterministic():
    results = []
    with App(Button("a")) as app:
        timer = app.call_later(.1, lambda: results.append("timer"), repeat=True)
        app.call_from_thread(lambda: results.append("queued"))
        app.render(dt=.05)
        assert results == ["queued"]
        app.render(dt=.05)
        assert results == ["queued", "timer"]
        timer.cancel()
        app.render(dt=.2)
        assert len(results) == 2


@pytest.mark.parametrize("size", [(0, 0), (1, 1), (12, 4), (100, 32), (160, 50)])
def test_entire_gallery_all_tabs_at_multiple_sizes(size):
    with gallery() as app:
        tabs = next(w for w in app.root.walk() if isinstance(w, Tabs))
        for i in range(4):
            tabs.select(i)
            app.render(*size, dt=.05)
            app.encode(app.render(*size, dt=.1))


def test_gallery_first_frame_and_scrolled_offscreen_children():
    with gallery() as app:
        app.render(80, 24, dt=.0001)
        for widget in app.root.walk():
            if isinstance(widget, ScrollView):
                widget.scroll_by(9)
        for _ in range(60):
            app.render(80, 24, dt=1 / 60)


def test_example_builders_render():
    root = Path(__file__).resolve().parents[1]
    for name in ("hello", "animated_menu", "dashboard", "pixel_oled"):
        spec = importlib.util.spec_from_file_location(name, root / "examples" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with module.build() as app:
            app.render(80, 24, dt=.1)
