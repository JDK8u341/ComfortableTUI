import io
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from comfortable_tui import App, Button, Key, Label
from comfortable_tui.terminal import Terminal


class TTY(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


def test_terminal_restores_on_exception_and_pops_only_its_keyboard_mode(monkeypatch):
    restored = []
    output = TTY()
    terminal = Terminal(input=TTY(), output=output)
    if os.name == "nt":
        def configure():
            terminal._restore.append(lambda: restored.append("windows"))
        monkeypatch.setattr(terminal, "_windows_modes", configure)
    else:
        monkeypatch.setitem(sys.modules, "termios", SimpleNamespace(
            TCSADRAIN=1, tcgetattr=lambda fd: "old",
            tcsetattr=lambda fd, when, state: restored.append(state)))
        monkeypatch.setitem(sys.modules, "tty", SimpleNamespace(setraw=lambda fd: None))
    with pytest.raises(RuntimeError, match="test"):
        with terminal:
            terminal.enable_keyboard()
            terminal.enable_keyboard()
            raise RuntimeError("test")
    data = output.getvalue()
    assert restored
    assert data.count("\x1b[>3u") == 1
    assert data.count("\x1b[<u") == 1
    assert "\x1b[?25h" in data and "\x1b[?1049l" in data
    output = TTY()
    Terminal(input=TTY(), output=output).__exit__(None, None, None)
    assert "\x1b[<u" not in output.getvalue()


def test_noninteractive_terminal_is_rejected():
    with pytest.raises(RuntimeError, match="interactive terminal"):
        with Terminal(input=io.StringIO(), output=io.StringIO()):
            pass


@pytest.mark.skipif(os.name != "nt", reason="Windows extended key input")
def test_windows_extended_f6_and_shift_tab_are_decoded(monkeypatch):
    import msvcrt
    from collections import deque
    pending = deque(["\x00", "@", "\x00", "\x0f"])
    monkeypatch.setattr(msvcrt, "kbhit", lambda: bool(pending))
    monkeypatch.setattr(msvcrt, "getwch", pending.popleft)
    terminal = Terminal(input=TTY(), output=TTY())
    assert terminal.read(0) == [Key("f6"), Key("shift+tab")]


def test_worker_results_and_errors_execute_on_ui_thread():
    main_thread = threading.get_ident()
    done, results = threading.Event(), []
    with App(Button("root")) as app:
        def work():
            return threading.get_ident()
        future = app.run_worker(work, lambda value: (results.append((value, threading.get_ident())), done.set()))
        worker_id = future.result(timeout=3)
        assert worker_id != main_thread and not done.is_set()
        app.render()
        assert results == [(worker_id, main_thread)]
        def fail():
            raise ValueError("expected failure")
        errors = []
        future = app.run_worker(fail, lambda _: None, on_error=lambda exc: errors.append(str(exc)))
        with pytest.raises(ValueError):
            future.result(timeout=3)
        # Future.result may return before completion callbacks finish queuing.
        ready = threading.Event()
        app._executor.submit(lambda: ready.set()).result(timeout=3)
        app.render()
        assert errors == ["expected failure"]


def test_run_ticks_once_and_cleans_up_if_paint_raises(monkeypatch):
    import comfortable_tui.app as module
    events = []

    class Failing(Label):
        def tick(self, dt):
            events.append("tick")

        def paint(self, canvas):
            raise ValueError("paint error")

    class FakeTerminal:
        size = (80, 24)

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("restored")

        def write(self, data):
            pass

        def read(self, timeout):
            return [Key("ctrl+q")]

    monkeypatch.setattr(module, "Terminal", FakeTerminal)
    with App(Failing("test")) as app:
        with pytest.raises(ValueError, match="paint error"):
            app.run()
        assert not app._running
    assert events == ["tick", "restored"]


def test_ownership_cycles_and_zero_delay_timer_deferral():
    from comfortable_tui import VBox
    parent = VBox()
    child = VBox()
    parent.add(child)
    with pytest.raises(ValueError):
        child.add(parent)
    with App(parent) as app:
        with pytest.raises(ValueError):
            App(child)
        events = []
        def first():
            events.append("first")
            app.call_later(0, lambda: events.append("second"))
        app.call_later(0, first)
        app.render(dt=0)
        assert events == ["first"]
        app.render(dt=0)
        assert events == ["first", "second"]
