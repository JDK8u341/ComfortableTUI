import math

import pytest

from comfortable_tui import Animator, Value


@pytest.mark.parametrize("damping", [0, .4, 1, 2.5])
def test_same_motion_at_different_frame_rates(damping):
    positions = []
    for fps in (30, 60, 120):
        v = Value(0, speed=12, damping=damping)
        v.set(100)
        for _ in range(fps // 5):
            v.step(1 / fps)
        positions.append((v.current, v.velocity))
    for position in positions[1:]:
        assert position == pytest.approx(positions[0], abs=1e-9)


@pytest.mark.parametrize("damping", [0, .4, 1, 4])
def test_long_stall_is_stable_and_settles(damping):
    v = Value(0, damping=damping)
    v.set(100)
    v.step(100)
    assert v.current == 100
    assert not v.active
    assert not v.step(.01)


def test_retarget_keeps_momentum_and_underdamped_spring_overshoots():
    v = Value(0, damping=.5)
    v.set(100)
    peak = 0
    for _ in range(120):
        v.step(1 / 120)
        peak = max(peak, v.current)
    assert peak > 110
    v.set(0)
    velocity = v.velocity
    assert v.velocity == velocity
    v.kick(2)
    assert v.velocity > velocity


def test_reduced_motion_and_explicit_ownership():
    manager = Animator(reduced_motion=True)
    v = manager.add(Value())
    manager.add(v)
    assert len(manager.values) == 1
    v.set(22)
    assert manager.step(.01)
    assert v.current == 22
    manager.remove(v)
    assert not manager.active


@pytest.mark.parametrize("dt", [-1, math.inf, math.nan])
def test_invalid_dt(dt):
    with pytest.raises(ValueError):
        Value().step(dt)
