"""User-visible spring behaviour, teardown and terminal-cell compositing."""
import pytest

from comfortable_tui import (App, BarChart, Button, Canvas, Checkbox, Key, Label,
                             MotionProfile, Panel, Rect, Spring, Style, Switch, Value, VBox)


def settle(app, seconds=1.5):
    for _ in range(round(seconds * 60)):
        frame = app.render(60, 18, dt=1 / 60)
    return frame


def test_expressive_focus_overshoots_but_subtle_focus_is_monotonic():
    for profile, bouncy in (("expressive", True), ("subtle", False)):
        first, last = Button("first", height=1), Button("last", height=1)
        with App(Panel(VBox(first, last, gap=4)), motion=profile) as app:
            settle(app)
            app.focus(last)
            positions = []
            for _ in range(90):
                app.render(60, 18)
                positions.append(app._focus_highlight.edges[1].current)
            assert (max(positions) > last.rect.y + .02) == bouncy
            assert positions[-1] == last.rect.y


def test_changing_profile_and_reversing_preserves_momentum():
    first, last = Button("first", height=1), Button("last", height=1)
    with App(VBox(first, last, gap=3)) as app:
        settle(app)
        app.focus(last)
        app.render(60, 18, dt=.04)
        edge = app._focus_highlight.edges[1]
        state = edge.current, edge.velocity
        app.set_motion(MotionProfile(focus=Spring(.4, .5)))
        app.focus(first)
        app.render(60, 18, dt=0)
        assert (edge.current, edge.velocity) == state
        settle(app)
        assert not app.animator.active


def test_button_feedback_does_not_move_text_and_checkbox_callbacks_are_immediate():
    changes = []
    button = Button("Save", height=1)
    checkbox = Checkbox("Enabled", on_change=changes.append, height=1)
    with App(Panel(VBox(button, checkbox, gap=2))) as app:
        before = settle(app)
        button.activate()
        checkbox.activate()
        assert changes == [True] and checkbox.checked
        during = app.render(60, 18, dt=.04)
        row = button.rect.y
        assert before.plain().splitlines()[row] == during.plain().splitlines()[row]
        assert before.style_at(10, row) != during.style_at(10, row)
        assert 0 < checkbox.checked_mix.current < 1


def test_programmatic_switch_state_animates_and_reduced_motion_snaps():
    switch = Switch("Pause")
    with App(switch) as app:
        settle(app)
        switch.checked = True
        app.render(60, 18, dt=.04)
        assert 0 < switch.position.current < 1
        switch.checked = False
        settle(app)
        assert switch.position.current == 0
    with App(Switch("Pause"), reduced_motion=True) as app:
        app.render()
        app.root.activate()
        app.render()
        assert app.root.position.current == 1 and not app.animator.active


def test_modal_animates_without_delaying_focus_or_retaining_a_live_widget():
    root = Button("root")
    app = App(root)
    try:
        settle(app)
        baseline = len(app.animator.values)
        modal = Panel(Button("close"), "Dialog", width=30, height=7)
        app.show_modal(modal)
        app.render(60, 18, dt=.01)
        entering_y = modal.rect.y
        assert app.focused is modal.children[0]
        settle(app)
        assert modal.rect.y < entering_y
        app.dispatch(Key("escape"))
        assert app.modal is None and app.focused is root and modal.app is None
        assert len(app.animator.values) == baseline
        app.render(60, 18, dt=.03)
        assert app._modal_picture is not None
        app.render(60, 18, dt=.18)
        assert app._modal_picture is None and not app._modal_motion.active
        frame = settle(app)
        assert app._modal_picture is None and "Dialog" not in frame.plain()
    finally:
        app.close()
    assert not app.animator.values


def test_notification_has_an_exit_transition_and_reduced_motion_cleans_immediately():
    for reduced in (False, True):
        with App(Label("background"), reduced_motion=reduced) as app:
            app.notify("Saved", duration=.2)
            app.render(60, 18, dt=.1)
            assert app._toast == "Saved"
            app.render(60, 18, dt=.1)
            if reduced:
                assert not app._toast
            else:
                assert app._toast and app._toast_motion.target == 0
            assert "Saved" not in settle(app).plain()


def test_chart_interpolation_preserves_exact_numbers_and_bounded_animation_ownership():
    chart = BarChart({"CPU 0": 20, "CPU 1": 100})
    with App(chart) as app:
        settle(app)
        old = chart._bars["CPU 0"].current
        baseline = len(app.animator.values)
        chart.samples = {"CPU 0": 80, "CPU 1": 100}
        frame = app.render(60, 18, dt=.02)
        assert old < chart._bars["CPU 0"].current < .8
        assert "80" in frame.plain()
        for i in range(20):
            chart.samples = {f"new {i}": 50, "CPU 1": 100}
            app.render(60, 18)
            assert len(app.animator.values) == baseline
        settle(app)
        assert not app.animator.active


@pytest.mark.parametrize("maximum,expected", [(None, 1), (100, .586)])
def test_chart_render_uses_selected_scale_and_keeps_exact_sample(maximum, expected):
    chart = BarChart({"CPU 0": 38.6, "CPU 1": 22.9, "CPU 2": 58.6}, maximum=maximum)
    with App(chart, reduced_motion=True) as app:
        app.render(80, 3)
        frame = app.render(80, 3)
        row = frame.plain().splitlines()[2]
        bar = row[15:73]  # Rendered track, excluding labels and numeric value.
        fraction = sum(" ▏▎▍▌▋▊▉█".index(c) for c in bar) / (8 * len(bar))
        assert fraction == pytest.approx(expected, abs=1 / (8 * len(bar)))
        assert "58.6" in row
        if maximum == 100:
            chart.samples["CPU 0"] = 90
            app.render(80, 3)
            updated = app.render(80, 3).plain().splitlines()[2]
            assert updated[15:73] == bar  # Another core must not change this core's scale.


@pytest.mark.parametrize("maximum", [0, -1, float("nan"), float("inf")])
def test_chart_rejects_invalid_fixed_scale(maximum):
    with pytest.raises(ValueError, match="maximum"):
        BarChart({}, maximum=maximum)


def test_cell_snapshot_compositing_keeps_wide_glyphs_and_excludes_images():
    source = Canvas(8, 3, Style(bg=(100, 100, 100)))
    source.text(2, 1, "中a")
    source.overlays[1] = object()
    source.graphics[1] = object()
    target = Canvas(8, 3, Style(bg=(20, 20, 20)))
    target.blit(source, Rect(2, 1, 4, 1), x=1, y=1, opacity=.5)
    assert target.cells[9].text == "中" and target.cells[10].width == 0
    assert not target.graphics and not target.overlays
    clipped = Canvas(3, 1)
    clipped.blit(source, Rect(3, 1, 3, 1))
    assert clipped.cells[0].text == " " and all(c.width != 0 for c in clipped.cells)


def test_opaque_blit_handles_overlap_and_tint_preserves_wide_owners():
    frame = Canvas(8, 3)
    frame.text(0, 0, "中abcd")
    frame.blit(frame, Rect(0, 0, 6, 1), x=1, y=1)
    assert frame.cells[9].text == "中" and frame.cells[10].width == 0
    frame.tint(0, 0, 8, 3, Style(fg=(200, 150, 100), bg=(100, 90, 80)), opacity=.4)
    assert frame.cells[9].style == frame.cells[10].style
    with frame.clipped(Rect(2, 0, 5, 3)):
        frame.blit(frame, Rect(0, 0, 6, 1), x=1, y=2)
    assert frame.cells[18].width != 0


@pytest.mark.parametrize("kwargs", [{"response": 0}, {"response": float("nan")}, {"damping": 0}])
def test_invalid_spring_tokens_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Spring(**kwargs)


def test_data_motion_never_overshoots_and_custom_values_keep_their_parameters():
    custom = Value(speed=9, damping=0)
    with App(Label("test")) as app:
        app.animator.add(custom)
        data = app.animator.add(Value(role="data"))
        data.set(1)
        values = []
        for _ in range(120):
            app.render(dt=1 / 120)
            values.append(data.current)
        assert max(values) <= 1 and values == sorted(values)
        assert (custom.speed, custom.damping) == (9, 0)
        app.animator.remove(custom)
        app.animator.remove(data)
