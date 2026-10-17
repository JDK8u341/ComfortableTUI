"""Child-only display environment; never changes desktop/session configuration."""
import os
from pathlib import Path
import sys


_WSLG_RUNTIME = Path("/mnt/wslg/runtime-dir")


def display_environment(platform, environment=None):
    env = dict(os.environ if environment is None else environment)
    if (sys.platform != "linux" or platform == "x11" or not env.get("WSL_DISTRO_NAME")
            or "WAYLAND_SOCKET" in env or env.get("WAYLAND_DISPLAY", "wayland-0") != "wayland-0"):
        return env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and (Path(runtime) / "wayland-0").is_socket():
        return env
    # WSL/systemd may recreate /run/user/$UID after WSLg added its symlink.
    # libwayland 1.15+ accepts an absolute WAYLAND_DISPLAY. Keep the user's
    # private XDG_RUNTIME_DIR intact and change only these child processes.
    socket = _WSLG_RUNTIME / "wayland-0"
    if socket.is_socket():
        env["WAYLAND_DISPLAY"] = str(socket)
    return env
