"""Control-plane GPU API tests; hardware tests live in verify_gpu_windows.py."""
import pickle
import subprocess
import sys

import pytest

from comfortable_tui import App, GpuFrameInfo, GpuFrameSource, OverlayCanvas, Text


def info(**changes):
    args = dict(name="Local\\CTUI_GPU_" + "a" * 32, width=64, height=32, adapter_low=1, adapter_high=0)
    args.update(changes)
    return GpuFrameInfo(**args)


@pytest.mark.parametrize("change", [{"name": "bad"}, {"width": 0}, {"height": 20000}, {"width": 3.5},
                                    {"version": 2}, {"adapter_low": -1}, {"adapter_high": 2**31},
                                    {"pixel_format": "rgba8"}])
def test_descriptor_validation(change):
    with pytest.raises(ValueError):
        info(**change)


def test_descriptor_handshake_is_picklable_and_headless_needs_no_frame_calls():
    descriptor = pickle.loads(pickle.dumps(info()))
    source = GpuFrameSource(descriptor)
    assert not hasattr(source, "read") and not hasattr(source, "sequence")
    view = OverlayCanvas(source, width=12, height=4)
    with App(Text("GPU ", view)) as app:
        frame = app.render(40, 10)
        assert frame.overlays[id(view)][0] is view
        assert not app._overlay_sessions
    source.close()
    with pytest.raises(RuntimeError, match="closed"):
        OverlayCanvas(source)


def test_base_import_does_not_load_gpu_driver_or_qt():
    subprocess.run([sys.executable, "-c", "import comfortable_tui, sys; "
                    "assert 'comfortable_tui._native_gpu' not in sys.modules; "
                    "assert not any(k.startswith('PySide6') for k in sys.modules)"], check=True)


def test_attach_reports_missing_native_extension(monkeypatch):
    from comfortable_tui import gpu
    monkeypatch.setattr(gpu, "gpu_available", lambda: False)
    with pytest.raises(RuntimeError, match="COMFORTABLE_TUI_BUILD_GPU"):
        GpuFrameSource.attach(info().name)


def test_native_control_module_rejects_invalid_window_without_creating_device():
    native = pytest.importorskip("comfortable_tui._native_gpu")
    assert native.protocol_version == 1
    with pytest.raises(ValueError, match="HWND"):
        native.Compositor(0)
