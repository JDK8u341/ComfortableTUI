"""Optional browser widget built on the unchanged image canvas interface."""
from __future__ import annotations

from .overlay import OverlayCanvas


_VIRTUAL_KEYS = {"left": 37, "up": 38, "right": 39, "down": 40, "enter": 13, "tab": 9,
                 "backspace": 8, "delete": 46, "escape": 27, "home": 36, "end": 35,
                 "pageup": 33, "pagedown": 34, "space": 32, "insert": 45}
_VIRTUAL_KEYS.update({f"f{i}": 111 + i for i in range(1, 25)})


class WebView(OverlayCanvas):
    """Display a BrowserPage and adapt canvas events to its independent service.

    A page may have multiple views; these mirror one document. Distinct documents
    require service.open(). Tab traverses TUI controls; click/Enter enters page
    interaction and Escape leaves it. F6 always returns to the TUI menu.
    """
    def __init__(self, page, *, on_event=None, on_browser_event=None, **kwargs):
        self.page, self._user_canvas_event = page, on_event
        self.interacting = False
        self._enter_key_pending = False
        self._user_browser_event = on_browser_event
        self._remove_listener = page.on_event(self._browser_event)
        super().__init__(page.source, on_event=self._canvas_event, **kwargs)
        page._widgets.add(self)

    @property
    def native_focus(self):
        return self.focused and self.interacting

    def enter_interaction(self):
        """Explicitly give the browser keys; Tab/F6 still belong to the TUI."""
        if self._closed or self.page._closed or self.disabled:
            return False
        if self.app and not self.app.focus(self):
            return False
        if not self.interacting:
            self.interacting = True
            self.page.input("focus_in")
            self.invalidate()
        return True

    def leave_interaction(self):
        if self.interacting:
            self.interacting = False
            if not self.page._closed:
                self.page.input("focus_out")
            self.invalidate()

    def _focus_changed(self, focused):
        if not focused:
            self.leave_interaction()

    def _navigation_parent(self):
        from .data import ListView
        parent = self.parent
        while parent:
            if isinstance(parent, ListView):  # includes DataTable and Tree
                return parent
            parent = parent.parent
        return None

    def mount(self, app):
        super().mount(app)
        self._remove_app_poll = self.page.service._bind_app(app)

    def unmount(self):
        stop = getattr(self, "_remove_app_poll", None)
        if stop:
            stop()
            self._remove_app_poll = None
        super().unmount()

    def _browser_event(self, event):
        if event["event"] == "ime" and "cursor" in event.get("data", {}):
            self.set_ime(cursor=event["data"]["cursor"])
        if self._user_browser_event:
            self._user_browser_event(event)
        self.invalidate()

    def tick(self, dt):
        self.page.service.poll()
        super().tick(dt)
        if self.page._closed:
            return
        visible = self._page_visible()
        if getattr(self.page, "_reported_visible", None) != visible:
            self.page._reported_visible = visible
            self.page.service._rpc.send("visible", view=self.page.id, visible=visible)

    def _page_visible(self):
        return any(widget.app and id(widget) in widget.app._overlay_placements and not widget._closed
                   and getattr(widget, "_browser_display_visible", None) is not False
                   for widget in self.page._widgets)

    def _canvas_event(self, event):
        user_consumed = bool(self._user_canvas_event(event)) if self._user_canvas_event else False
        kind, data = event.kind, event.data
        if kind == "buffer_attached":
            self._browser_attached_source = data.get("buffer")
            self.page._collect_buffers()
            return user_consumed
        if kind == "detached":
            self._browser_attached_source = None
            self._browser_display_visible = False
            if self._closed:
                self.page._widgets.discard(self)
            self.page._collect_buffers()
        if self.page._closed:
            return user_consumed
        if kind in ("show", "hide", "detached"):
            if kind != "show":
                self.leave_interaction()
            self._browser_display_visible = kind == "show"
            visible = self._page_visible()
            if getattr(self.page, "_reported_visible", None) != visible:
                self.page._reported_visible = visible
                self.page.service._rpc.send("visible", view=self.page.id, visible=visible)
            return False
        if user_consumed:
            return True
        if kind == "focus_out":
            self.leave_interaction()
            return False
        if kind == "mouse_down" and data.get("inside_source", True):
            self.enter_interaction()
        if kind in ("key_down", "key_up"):
            name = data.get("name", "")
            if name == "enter" and kind == "key_up" and self._enter_key_pending:
                self._enter_key_pending = False
                return True
            if name in ("tab", "shift+tab", "f6"):
                self.leave_interaction()
                return False
            if self.app and name in self.app.bindings:
                return False
            if name == "escape" and self.interacting:
                self.leave_interaction()
                parent = self._navigation_parent()
                if self.app and parent:
                    self.app.focus(parent)
                return True
            if not self.interacting:
                if name == "enter":
                    if kind == "key_down":
                        self._enter_key_pending = True
                        self.enter_interaction()
                    return True
                if kind == "key_down" and name in ("left", "right", "up", "down", "home", "end", "pageup", "pagedown"):
                    parent = self._navigation_parent()
                    if self.app and parent:
                        self.app.focus(parent)
                return False
        if kind in ("text", "ime", "focus_in", "wheel") and not self.interacting:
            return False
        fields = {"modifiers": list(data.get("modifiers", ())) }
        if kind.startswith("mouse_") or kind == "wheel":
            point = data.get("source_position")
            if point is None:
                if kind != "mouse_leave":
                    return False
            else:
                fields.update(x=round(point[0]), y=round(point[1]))
            fields["button"] = {1: "left", 2: "right", 4: "middle"}.get(data.get("button"), "left")
            fields["buttons"] = data.get("buttons", 0)
            fields["clicks"] = 2 if kind == "mouse_double_click" else 1
            if kind == "mouse_double_click":
                kind = "mouse_down"
            if kind == "wheel":
                delta = data.get("angle_delta", (0, 0))
                if not any(delta):
                    delta = data.get("pixel_delta", (0, 0))
                fields.update(dx=round(delta[0]), dy=round(delta[1]))
        elif kind in ("key_down", "key_up"):
            name = data.get("name", "").split("+")[-1]
            if self.app and data.get("name") in self.app.bindings:
                return False
            if name in ("f6", "ctrl+q", "ctrl+c") or data.get("name") in ("ctrl+q", "ctrl+c"):
                return False
            code = _VIRTUAL_KEYS.get(name)
            if code is None:
                code = ord(name.upper()) if len(name) == 1 else data.get("virtual_key", 0)
            fields.update(key_code=code, scan_code=data.get("scan_code", 0), text=data.get("text", ""),
                          repeat=bool(data.get("repeat")))
        elif kind in ("text", "ime"):
            fields.update(commit=data.get("commit", ""), preedit=data.get("preedit", ""),
                          replacement_start=data.get("replacement_start", 0),
                          replacement_length=data.get("replacement_length", 0))
            fields["attributes"] = data.get("attributes", [])
        elif kind not in ("focus_in", "focus_out"):
            return False
        self.page.input(kind, **fields)
        return True

    def close(self):
        if not self._closed:
            self.leave_interaction()
            self._remove_listener()
        super().close()
        if self._detached:
            self.page._widgets.discard(self)
            self.page._collect_buffers()
