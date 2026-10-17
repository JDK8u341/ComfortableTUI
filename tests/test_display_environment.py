from pathlib import Path

from comfortable_tui import _display_environment as display


def test_wslg_missing_runtime_link_is_resolved_only_for_child(monkeypatch, tmp_path):
    monkeypatch.setattr(display.sys, "platform", "linux")
    wslg = tmp_path / "wslg"
    monkeypatch.setattr(display, "_WSLG_RUNTIME", wslg)
    monkeypatch.setattr(Path, "is_socket", lambda path: path == wslg / "wayland-0")
    original = dict(WSL_DISTRO_NAME="Ubuntu", WAYLAND_DISPLAY="wayland-0", XDG_RUNTIME_DIR=str(tmp_path / "runtime"))
    child = display.display_environment("wayland", original)
    assert child["WAYLAND_DISPLAY"] == str(wslg / "wayland-0")
    assert child["XDG_RUNTIME_DIR"] == original["XDG_RUNTIME_DIR"]
    assert original["WAYLAND_DISPLAY"] == "wayland-0"
    assert display.display_environment("x11", original) == original
    for override in ({"WAYLAND_DISPLAY": "other-compositor"}, {"WAYLAND_DISPLAY": "/explicit/socket"},
                     {"WAYLAND_SOCKET": "5"}, {"WSL_DISTRO_NAME": ""}):
        env = dict(original, **override)
        assert display.display_environment("wayland", env) == env
