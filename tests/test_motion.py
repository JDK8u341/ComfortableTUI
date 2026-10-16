"""Visual-state regressions: colors, moving geometry and interruptible menus."""
import pytest
import pyte

from comfortable_tui import (App, Button, Canvas, Checkbox, Input, Key, Label, ListView, Panel,
                             Rect, Style, Tabs, Theme, VBox)


def settle(app, width=60, height=12):
    for _ in range(8):
        frame = app.render(width, height, dt=.5)
    return frame


def test_unfocused_controls_and_panel_border_keep_parent_surface():
    first = Input("first", height=1)
    second = Checkbox("second", height=1)
    panel = Panel(VBox(first, second, gap=1))
    with App(panel) as app:
        settle(app)
        app.focus(first)
        settle(app)
        app.focus(second)
        frame = settle(app)
        # No dark rectangles remain at the previous input, its padding or border.
        assert {frame.style_at(x, first.rect.y).bg for x in range(60)} == {app.theme.panel.bg}
        assert all(frame.style_at(x, 0).bg == app.theme.panel.bg for x in range(60))


@pytest.mark.parametrize("theme", [Theme(), Theme.light()])
def test_custom_parent_surface_is_inherited_in_both_themes(theme):
    field, button = Input(placeholder="name", height=1), Button("save", height=1)
    surface = Style(bg=(53, 63, 73))
    with App(Panel(VBox(field, button, gap=1), style=surface), theme=theme) as app:
        settle(app)
        app.focus(button)
        frame = settle(app)
        assert all(frame.style_at(x, field.rect.y).bg == surface.bg for x in range(60))


def test_shared_focus_moves_continuously_without_touching_text():
    first, second = Button("one", height=1), Button("two", height=1)
    with App(Panel(VBox(first, second, gap=2))) as app:
        before = settle(app)
        app.focus(second)
        frame = app.render(60, 12, dt=1 / 120)
        _, y, _, height = app._focus_highlight.bounds
        assert first.rect.y < y < second.rect.y
        assert height > 1  # The leading edge travels ahead of the trailing edge.
        assert frame.plain() == before.plain()
        assert any(frame.style_at(5, row).bg != app.theme.panel.bg
                   for row in range(first.rect.y, second.rect.y + 1))
        frame = settle(app)
        assert frame.style_at(5, first.rect.y).bg == app.theme.panel.bg
        assert frame.style_at(5, second.rect.y).bg == app.theme.selection.bg


def test_retargeting_shared_focus_preserves_current_geometry():
    fields = [Button(str(i), height=1) for i in range(3)]
    with App(Panel(VBox(*fields, gap=1))) as app:
        settle(app)
        app.focus(fields[2])
        app.render(60, 12, dt=.03)
        moving = app._focus_highlight.bounds
        app.focus(fields[1])
        app.render(60, 12, dt=0)
        assert app._focus_highlight.bounds == moving


def test_tint_preserves_wide_glyphs_and_interpolates_parent_background():
    base = Style(fg=(100, 100, 100), bg=(30, 40, 50))
    target = Style(fg=(200, 200, 200), bg=(130, 140, 150))
    frame = Canvas(8, 2, base)
    frame.text(1, 0, "中e\u0301", base)
    frame.tint(1.5, 0, 1, .5, target)
    assert frame.cells[1].text == "中" and frame.cells[2].width == 0
    assert frame.cells[1].style == frame.cells[2].style
    assert frame.cells[1].style.bg == (55, 65, 75)
    assert frame.style_at(0, 0) == base
    with frame.clipped(Rect(2, 0, 3, 1)):
        frame.tint(2, 0, 2, 1, target)
    assert frame.cells[1].style == frame.cells[2].style


def test_list_highlight_does_not_introduce_root_background_in_panel():
    listing = ListView(["first", "second", "third"])
    with App(Panel(listing)) as app:
        settle(app)
        listing.select(1)
        for _ in range(24):
            frame = app.render(60, 12, dt=1 / 120)
            for cell in frame.cells:
                assert cell.style.bg != Theme().background.bg


def test_tabs_animate_highlight_and_both_pages_with_correct_focus_scope():
    first, second = Input("first"), Input("second")
    tabs = Tabs({"First": first, "Longer second": second})
    with App(tabs) as app:
        settle(app)
        initial = tabs._highlight.bounds
        tabs.select(1)
        frame = app.render(60, 12, dt=1 / 120)
        assert first.visible and second.visible
        assert first not in list(tabs.focusables())
        assert second in list(tabs.focusables())
        assert initial[0] < tabs._highlight.bounds[0] < tabs._tab_bounds[1][0]
        assert first.rect.x < 0 < second.rect.x < 60
        assert frame.plain().splitlines()[0].startswith(" First ")
        settle(app)
        assert not first.visible and second.rect.x == 0


def test_interrupted_tabs_preserve_page_velocity_and_settle_to_latest_selection():
    tabs = Tabs({name: Label(name) for name in ["Alpha", "Beta", "Gamma"]})
    with App(tabs) as app:
        settle(app)
        tabs.select(1)
        app.render(60, 12, dt=.04)
        value = tabs._offsets[tabs.children[0]]
        current, velocity = value.current, value.velocity
        assert velocity != 0
        tabs.select(0)
        assert (value.current, value.velocity) == (current, velocity)
        settle(app)
        assert tabs.selected == 0
        assert [child.visible for child in tabs.children] == [True, False, False]


def test_tabs_scroll_header_to_selected_title_and_clip_content_below_header():
    tabs = Tabs({"First": Label("A"), "Second": Label("B"), "最后一页": Label("C")})
    with App(tabs) as app:
        app.render(12, 6)
        tabs.select(2)
        frame = settle(app, 12, 6)
        assert tabs._bar_scroll.current > 0
        assert "最后一页" in frame.plain().splitlines()[0]
        assert "C" in frame.plain().splitlines()[2]


def test_header_wider_than_viewport_has_stable_scroll_target():
    tabs = Tabs({"First": Label("A"), "A very long menu title": Label("B")})
    with App(tabs) as app:
        app.render(8, 6)
        tabs.select(1)
        for _ in range(40):
            app.render(8, 6, dt=1 / 120)
            assert tabs._bar_scroll.target == tabs._tab_bounds[1][0]


def test_reduced_motion_finishes_focus_and_menu_in_one_frame():
    tabs = Tabs({"One": Button("one"), "Two": Button("two")})
    with App(tabs, reduced_motion=True) as app:
        app.render(40, 8)
        app.dispatch(Key("right"))
        app.render(40, 8)
        assert tabs._offsets[tabs.children[1]].current == 0
        assert tabs._highlight.bounds[0] == tabs._tab_bounds[1][0]
        assert not app.animator.active


def test_focus_color_diff_reconstructs_every_frame_in_terminal():
    from comfortable_tui import Renderer
    fields = [Input("hello", height=1), Checkbox("enable", height=1), Button("save", height=1)]
    with App(Panel(VBox(*fields, gap=1))) as app:
        renderer = Renderer(synchronized=True)
        screen = pyte.Screen(60, 12)
        stream = pyte.Stream(screen)
        for i in range(75):
            if i % 15 == 0:
                app.focus(fields[(i // 15) % len(fields)])
            frame = app.render(60, 12, dt=1 / 120)
            data = renderer.encode(frame)
            if i:
                assert "\x1b[2J" not in data
            stream.feed(data)
            for y in range(12):
                for x in range(60):
                    expected = frame.cells[y * 60 + x]
                    actual = screen.buffer[y][x]
                    assert actual.data == expected.text
                    assert actual.bg == "{:02x}{:02x}{:02x}".format(*expected.style.bg)
                    assert actual.reverse == expected.style.reverse


def test_no_color_retains_visible_focus_and_close_releases_highlight_values():
    controls = [Button("one", height=1), Button("two", height=1)]
    app = App(Panel(VBox(*controls, gap=1)), colors="none")
    try:
        frame = settle(app)
        assert frame.style_at(4, controls[0].rect.y).reverse
        app.focus(controls[1])
        frame = settle(app)
        assert not frame.style_at(4, controls[0].rect.y).reverse
        assert frame.style_at(4, controls[1].rect.y).reverse
        assert ";2;" not in app.encode(frame)
    finally:
        app.close()
    assert not app.animator.values


@pytest.mark.parametrize("fps", [30, 60, 120])
def test_tab_motion_at_equal_time_matches(fps):
    tabs = Tabs({"One": Label("1"), "Two": Label("2")})
    with App(tabs) as app:
        settle(app)
        tabs.select(1)
        for _ in range(fps // 5):
            app.render(60, 12, dt=1 / fps)
        # Independent expected exponential trailing edge after 200ms.
        import math
        goal = tabs._tab_bounds[1][0]
        assert tabs._highlight.bounds[0] == pytest.approx(goal * (1 - math.exp(-16 * .2)))
