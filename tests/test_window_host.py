from comfortable_tui import App, Checkbox, WindowHost


def test_native_window_space_uses_terminal_key_semantics():
    checkbox = Checkbox("Check")
    with App(checkbox) as app:
        app.render(20, 4)
        host = object.__new__(WindowHost)
        host._closed, host.app = False, app
        class Events:
            def events(self):
                return [{"event": "tui_event", "kind": "key_down", "data": {"name": "space", "text": " "}}]
        host._rpc = Events()
        host.poll()
        assert checkbox.checked
