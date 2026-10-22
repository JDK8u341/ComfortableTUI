"""Effect model contracts and the native schema boundary (no desktop input)."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from comfortable_tui import GlassCard, GlassControl, GlassEffect, GlassWorkspace


def test_reusable_effect_description_does_not_share_mutable_parameter_storage():
    values = [.3]
    effect = GlassEffect("tint", parameters=values)
    values[0] = 1
    assert effect.parameters == (.3,)
    with pytest.raises(FrozenInstanceError):
        effect.parameters = (1,)


def test_incremental_effect_update_is_transactional_and_rejects_replaced_nodes():
    shared = GlassEffect("tint", parameters=(.3,))
    first = GlassControl("first", "text", effects=[shared])
    second = GlassControl("second", "text", effects=[shared])
    card = GlassCard("card", "Card", [first, second])
    workspace = GlassWorkspace([card])
    updates = []
    workspace._native = SimpleNamespace(set_effect_parameters=lambda *args: updates.append(args))
    workspace.set_effect_parameters(card, first, 0, [.8])
    assert first.effects[0].parameters == (.8,)
    assert second.effects[0].parameters == shared.parameters == (.3,)
    assert updates == [("card", "first", first._generation, 0, "tint", [.8])]
    def fail(*args):
        raise ValueError("native schema rejected")
    workspace._native.set_effect_parameters = fail
    with pytest.raises(ValueError, match="schema"):
        workspace.set_effect_parameters(card, first, 0, [2])
    assert first.effects[0].parameters == (.8,)
    card.controls[0] = GlassControl("first", "text", effects=[shared])
    with pytest.raises(ValueError, match="replaced"):
        workspace.set_effect_parameters(card, first, 0, [.5])


def test_no_duplicate_or_unimplemented_effect_controls_are_silently_accepted():
    with pytest.raises(TypeError):
        GlassEffect("blur", blur=4)
    for effect in (GlassEffect("blur", target="invalid"), GlassEffect("liquid"),
                   GlassEffect("blur", opacity=.5), GlassEffect("tint", parameters=(float("inf"),))):
        with pytest.raises(ValueError):
            effect.snapshot()


def test_native_schema_is_the_authority_for_defaults_and_ranges():
    native = pytest.importorskip("comfortable_tui._native_glass")
    assert native.glass_protocol_version == 7
    assert native.effect_schema("blur") == [{"name": "radius", "minimum": 0., "maximum": 24., "default": 6.}]
    assert native.validate_effect_parameters("glow", []) == [1., 18.]
    for name, values in (("blur", [25]), ("blur", [1, 2]), ("glow", [float("nan")]), ("missing", [])):
        with pytest.raises(ValueError):
            native.validate_effect_parameters(name, values)
