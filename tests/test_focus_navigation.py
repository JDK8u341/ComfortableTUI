"""Directional focus must not bubble into an unrelated tab selection."""
import pytest

from comfortable_tui import (App, Button, DataTable, Grid, HBox, Input, Key, ListView, Panel,
                             InputParser, Rect, Slider, Tabs, Tree, TreeNode, Widget)
from comfortable_tui.demo import gallery


def settle(app, width=100, height=32):
    for _ in range(6):
        app.render(width, height, dt=.5)


def test_gallery_list_right_focuses_adjacent_table_without_switching_tabs():
    with gallery() as app:
        tabs = next(w for w in app.root.walk() if isinstance(w, Tabs))
        tabs.select(1)
        settle(app)
        listing = next(w for w in tabs.children[1].walk() if type(w) is ListView)
        table = next(w for w in tabs.children[1].walk() if isinstance(w, DataTable))
        listing.select(7)
        app.focus(listing)
        app.dispatch(Key("right"))
        assert app.focused is table
        assert tabs.selected == 1
        assert listing.selected == 7


def test_content_arrows_do_not_select_tabs_even_without_a_neighbor():
    content = Button("only control")
    tabs = Tabs({"one": content, "two": Button("other page")})
    with App(tabs) as app:
        settle(app)
        app.focus(content)
        app.dispatch(Key("right"))
        assert tabs.selected == 0 and app.focused is content


def test_table_at_horizontal_scroll_boundary_returns_focus_to_list():
    listing = ListView(["one", "two"])
    table = DataTable(["Name"], [("item",)])
    with App(HBox(Panel(listing), Panel(table))) as app:
        settle(app)
        app.focus(table)
        app.dispatch(Key("left"))
        assert app.focused is listing


def test_only_focused_tab_bar_uses_arrows_to_select_pages():
    tabs = Tabs({"one": Button("one"), "two": Button("two")})
    with App(tabs) as app:
        settle(app)
        app.focus(tabs)
        app.dispatch(Key("right"))
        assert tabs.selected == 1 and app.focused is tabs
        app.dispatch(Key("left", phase="release"))
        assert tabs.selected == 1
        app.dispatch(Key("left"))
        assert tabs.selected == 0


def test_arrow_navigation_follows_grid_geometry():
    controls = [Button(str(i)) for i in range(4)]
    with App(Grid(*controls, columns=2)) as app:
        settle(app)
        app.focus(controls[0])
        for direction, expected in [("right", 1), ("down", 3), ("left", 2), ("up", 0)]:
            app.dispatch(Key(direction))
            assert app.focused is controls[expected]
        app.dispatch(Key("left"))
        assert app.focused is controls[0]


def test_table_scroll_has_priority_before_focus_navigation():
    listing = ListView(["one"])
    table = DataTable(["Name", "Value"], [("item", "value")], column_widths=[30, 30])
    with App(HBox(Panel(listing), Panel(table))) as app:
        settle(app)
        table.horizontal_offset = 4
        app.focus(table)
        app.dispatch(Key("left"))
        assert app.focused is table and table.horizontal_offset == 0
        app.dispatch(Key("left"))
        assert app.focused is listing


def test_input_slider_and_tree_consume_their_own_arrows():
    field = Input("hello")
    slider = Slider(30)
    root = TreeNode("root", [TreeNode("child")])
    tree = Tree(root)
    with App(HBox(field, slider, tree)) as app:
        settle(app)
        app.focus(field)
        app.dispatch(Key("left"))
        assert app.focused is field and field.cursor == 4
        app.focus(slider)
        app.dispatch(Key("right"))
        assert app.focused is slider and slider.value_ == 31
        app.focus(tree)
        app.dispatch(Key("right"))
        assert app.focused is tree and root.expanded


def test_modal_and_inactive_tabs_are_excluded_from_spatial_navigation():
    first, second = Button("first"), Button("second")
    tabs = Tabs({"one": first, "two": second})
    with App(tabs) as app:
        settle(app)
        tabs.select(1)
        settle(app)
        app.focus(second)
        assert not app.focus_direction("left")
        assert app.focused is second
        only = Button("modal")
        app.show_modal(Panel(only, width=30, height=8))
        settle(app)
        app.dispatch(Key("left"))
        assert app.focused is only and tabs.selected == 1


def test_hidden_and_disabled_controls_are_skipped_and_release_does_not_move_focus():
    first, last = Button("first"), Button("last")
    with App(HBox(first, Button("disabled", disabled=True), Button("hidden", visible=False), last)) as app:
        settle(app)
        app.focus(first)
        app.dispatch(Key("right", phase="release"))
        assert app.focused is first
        app.dispatch(Key("right"))
        assert app.focused is last


def test_spatial_candidates_aligned_with_source_beat_diagonal_controls():
    first, far, diagonal = Button("source"), Button("aligned"), Button("diagonal")
    with App(Widget()) as app:
        app.root.add(first, far, diagonal)
        app.render(100, 32)
        first.layout(Rect(0, 0, 10, 4))
        far.layout(Rect(70, 0, 10, 4))
        diagonal.layout(Rect(15, 10, 10, 4))
        app.focus(first)
        assert app.focus_direction("right")
        assert app.focused is far


def test_clipped_out_controls_are_not_spatial_candidates():
    first, outside = Button("source"), Button("clipped")
    parent = Widget()
    parent.add(first, outside)
    with App(parent) as app:
        app.render(100, 32)
        first.layout(Rect(0, 0, 10, 4))
        outside.layout(Rect(100, 0, 10, 4))
        app.focus(first)
        assert not app.focus_direction("right")


def test_invalid_direction_is_rejected():
    with App(Button("a")) as app:
        with pytest.raises(ValueError):
            app.focus_direction("diagonal")


def test_f6_focuses_menu_and_returns_to_previous_content_control():
    first, second = Button("first"), Button("second")
    tabs = Tabs({"one": HBox(first, second), "two": Button("other")})
    with App(tabs) as app:
        settle(app)
        app.focus(second)
        app.dispatch(Key("f6"))
        assert app.focused is tabs
        app.dispatch(Key("f6"))
        assert app.focused is second


def test_list_top_arrow_can_return_to_ancestor_menu_bar():
    listing = ListView(["one", "two"])
    tabs = Tabs({"one": Panel(listing), "two": Button("other")})
    with App(tabs) as app:
        settle(app)
        app.focus(listing)
        app.dispatch(Key("up"))
        assert app.focused is tabs
        app.dispatch(Key("down"))
        assert app.focused is listing


def test_menu_bar_focus_is_visually_distinct_from_selected_page():
    content = Button("content")
    tabs = Tabs({"one": content, "two": Button("other")})
    with App(tabs) as app:
        settle(app)
        app.focus(content)
        unfocused = app.render(100, 32, dt=.5).plain().splitlines()[0]
        app.focus(tabs)
        focused = app.render(100, 32, dt=.5).plain().splitlines()[0]
        assert focused != unfocused


@pytest.mark.parametrize("sequence", ["\x1b[17~", "\x1b[57369u"])
def test_f6_is_decoded_for_legacy_and_enhanced_terminals(sequence):
    content = Button("content")
    tabs = Tabs({"one": content})
    with App(tabs) as app:
        settle(app)
        app.focus(content)
        for event in InputParser().feed(sequence):
            app.dispatch(event)
        assert app.focused is tabs
        app.dispatch(Key("f6", phase="release"))
        assert app.focused is tabs
        app.dispatch(Key("enter"))
        assert app.focused is content


def test_menu_remains_reachable_by_tab_and_shift_tab_from_content():
    content = Button("content")
    tabs = Tabs({"one": content})
    with App(tabs) as app:
        settle(app)
        app.focus(content)
        for event in InputParser().feed("\x1b[Z"):
            app.dispatch(event)
        assert app.focused is tabs
        app.dispatch(Key("tab"))
        assert app.focused is content
        app.dispatch(Key("tab"))
        assert app.focused is tabs


def test_f6_stays_inside_modal_and_uses_valid_content_after_page_change():
    first, second = Button("first"), Button("second")
    tabs = Tabs({"one": first, "two": second})
    with App(tabs) as app:
        settle(app)
        app.focus(first)
        app.dispatch(Key("f6"))
        app.dispatch(Key("right"))
        settle(app)
        app.dispatch(Key("enter"))
        assert app.focused is second
        modal = Button("modal")
        app.show_modal(Panel(modal, width=30, height=8))
        settle(app)
        app.dispatch(Key("f6"))
        assert app.focused is modal


def test_arrow_boundaries_can_leave_list_but_page_movement_still_consumes_event():
    listing = ListView(["one", "two", "three"])
    with App(listing) as app:
        settle(app)
        assert listing.on_event(Key("pagedown"))
        assert listing.selected == 2
        assert not listing.on_event(Key("down"))


def test_gallery_f6_reaches_menu_from_table_and_restores_focus():
    with gallery() as app:
        tabs = next(w for w in app.root.walk() if isinstance(w, Tabs))
        tabs.select(1)
        settle(app)
        table = next(w for w in tabs.children[1].walk() if isinstance(w, DataTable))
        app.focus(table)
        app.dispatch(Key("f6"))
        assert app.focused is tabs
        app.dispatch(Key("f6"))
        assert app.focused is table and tabs.selected == 1
