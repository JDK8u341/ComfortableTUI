"""Compatibility error for the withdrawn experimental Linux window host.

The former Qt host drew the TUI in its own window. It did not integrate with an
existing terminal emulator and is no longer a supported graphical backend.
"""


class WindowHost:
    """Withdrawn entry point; use App.run() for a terminal-only Linux TUI."""

    def __init__(self, app, *, executable=None, display_platform=None, test_controls=False):
        raise NotImplementedError(
            "Linux graphical support is suspended: WindowHost was a separate Qt window, "
            "not an external terminal overlay. Use App.run() without OverlayCanvas/WebView "
            "on Linux; see docs/linux-support-decision.md"
        )
