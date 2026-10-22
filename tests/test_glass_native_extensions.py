"""Hardware-free SDK registration tests; CI builds the separate plugin first."""
import importlib.util
import os
from pathlib import Path

import pytest

from comfortable_tui import GlassCard, GlassControl, register_glass_controls


@pytest.fixture(scope="module")
def extension():
    pytest.importorskip("comfortable_tui._native_glass")
    folder = os.environ.get("COMFORTABLE_GLASS_EXTENSION")
    if not folder:
        pytest.skip("Set COMFORTABLE_GLASS_EXTENSION to the example plugin directory")
    path = next(Path(folder).glob("ctui_glass_controls*.pyd"))
    spec = importlib.util.spec_from_file_location("ctui_glass_controls", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register_glass_controls(module)
    return module


def test_registered_type_is_usable_without_hardware_and_registration_is_idempotent(extension):
    register_glass_controls(extension)
    card = GlassCard("mixer", "Mixer", [GlassControl("dial", "example.dial", value=.4)])
    assert card.snapshot()["controls"][0]["kind"] == "example.dial"
    from comfortable_tui import _native_glass
    assert _native_glass.measure_control("example.dial") == 100


def test_rejects_toolchain_mismatch(extension):
    from comfortable_tui import _native_glass
    with pytest.raises(ValueError, match="ABI/toolchain"):
        _native_glass.register_controls(extension, extension.bad_abi())


def test_registry_is_immutable_and_failed_batch_is_atomic(extension):
    from comfortable_tui import _native_glass
    with pytest.raises(ValueError, match="duplicate"):
        _native_glass.register_controls(extension, extension.duplicate())
    with pytest.raises(ValueError, match="duplicate"):
        _native_glass.register_controls(extension, extension.atomic_failure())
    with pytest.raises(ValueError, match="unregistered"):
        _native_glass.measure_control("example.never_registered")
    assert _native_glass.measure_control("button") == 46
