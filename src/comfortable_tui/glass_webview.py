"""Convenience constructor; generic card hosting is implemented by GlassCanvas."""
from __future__ import annotations

from .browser import BrowserPage
from .glass_canvas import GlassCanvas
from .webview import WebView


class GlassWebView(GlassCanvas):
    """Equivalent to GlassCanvas(id, WebView(page), auto_resize=True).

    Enter/click enters page interaction; Escape leaves, F6 returns to the
    terminal, and Tab traverses card controls. The caller owns the page/service.
    """
    def __init__(self, id, page, *, label="", height=240, enabled=True, fit="stretch",
                 auto_resize=True, on_event=None, on_browser_event=None):
        if not isinstance(page, BrowserPage):
            raise TypeError("GlassWebView needs a BrowserPage")
        if page._closed:
            raise RuntimeError("BrowserPage is closed")
        if type(auto_resize) is not bool:
            raise TypeError("auto_resize must be a bool")
        canvas = WebView(page, fit=fit, on_browser_event=on_browser_event)
        super().__init__(id, canvas, label=label, height=height, enabled=enabled,
                         fit=fit, auto_resize=auto_resize, on_event=on_event)
        self.page = page

    @property
    def interacting(self):
        return self.canvas.interacting

    def enter_interaction(self):
        if self._workspace is None or not self.enabled:
            return False
        return self.canvas.enter_interaction()

    def leave_interaction(self):
        self._lose_focus()
