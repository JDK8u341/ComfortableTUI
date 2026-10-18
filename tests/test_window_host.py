import pytest

from comfortable_tui import WindowHost


def test_withdrawn_window_host_rejects_before_starting_any_native_process():
    with pytest.raises(NotImplementedError, match="Linux graphical support is suspended"):
        WindowHost(None, executable="does-not-exist", display_platform="wayland")
