"""POSIX / Windows VT terminal lifecycle, restoring modes even on exceptions."""
from __future__ import annotations

import codecs
import os
import shutil
import sys
import time

from .events import InputParser


class Terminal:
    def __init__(self, *, input=None, output=None, mouse=True):
        self.input, self.output = input or sys.stdin, output or sys.stdout
        self.mouse = mouse
        self.parser = InputParser()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._restore = []
        self._last_input = 0.0
        self._special = False
        self._wide_decoder = codecs.getincrementaldecoder("utf-16-le")("replace")
        self._keyboard_pushed = False
        self._input_active = False
        self._input_stopped = False

    @property
    def size(self):
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines

    def __enter__(self):
        if not self.input.isatty() or not self.output.isatty():
            raise RuntimeError("An interactive terminal is required; use App.render() for headless rendering")
        try:
            if os.name == "nt":
                self._windows_modes()
            else:
                import termios
                import tty
                fd = self.input.fileno()
                state = termios.tcgetattr(fd)
                self._restore.append(lambda: termios.tcsetattr(fd, termios.TCSADRAIN, state))
                tty.setraw(fd)
            self._input_active = True
            self._input_stopped = False
            self.write("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?2004h")
            if self.mouse:
                self.write("\x1b[?1000h\x1b[?1002h\x1b[?1006h")
            # Capability negotiation: unsupported features stay disabled.
            self.write("\x1b[?2026$p\x1b[?u\x1b_Ga=q,i=31,s=1,v=1,f=24;AAAA\x1b\\")
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _windows_modes(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        for number, output in [(-10, False), (-11, True)]:
            handle = kernel.GetStdHandle(number & 0xffffffff)
            mode = wintypes.DWORD()
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                raise OSError("Windows console modes are unavailable")
            old = mode.value
            new = old | 0x0004 if output else (old & ~(0x0001 | 0x0002 | 0x0004 | 0x0040)) | 0x0200
            if not kernel.SetConsoleMode(handle, new):
                raise OSError("Virtual terminal mode could not be enabled")
            self._restore.append(lambda h=handle, m=old: kernel.SetConsoleMode(h, m))
        if hasattr(self.output, "reconfigure"):
            old_encoding, old_errors = self.output.encoding, self.output.errors
            self.output.reconfigure(encoding="utf-8", errors="replace")
            self._restore.append(lambda: self.output.reconfigure(encoding=old_encoding, errors=old_errors))

    def write(self, data):
        if data:
            self.output.write(data)
            self.output.flush()

    def enable_keyboard(self):
        if not self._keyboard_pushed:
            self.write("\x1b[>3u")
            self._keyboard_pushed = True

    def read(self, timeout=0.0):
        text = ""
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + timeout
            while True:
                while msvcrt.kbhit():
                    char = msvcrt.getwch()
                    if self._special:
                        text += {"H": "\x1b[A", "P": "\x1b[B", "K": "\x1b[D", "M": "\x1b[C",
                                 "G": "\x1b[H", "O": "\x1b[F", "S": "\x1b[3~",
                                 "I": "\x1b[5~", "Q": "\x1b[6~",
                                 "@": "\x1b[17~", "\x0f": "\x1b[Z"}.get(char, "")
                        self._special = False
                    elif char in ("\x00", "\xe0"):
                        self._special = True
                    else:
                        text += char
                if text or time.monotonic() >= deadline:
                    break
                time.sleep(min(0.002, max(0, deadline - time.monotonic())))
            # getwch can expose UTF-16 surrogate pairs for emoji.
            text = self._wide_decoder.decode(text.encode("utf-16-le", "surrogatepass"))
        else:
            import select
            if select.select([self.input], [], [], timeout)[0]:
                raw = os.read(self.input.fileno(), 65536)
                if not raw:
                    from .events import Key
                    return [Key("ctrl+c")]
                text = self._decoder.decode(raw)
        if text:
            self._last_input = time.monotonic()
        flush = not text and time.monotonic() - self._last_input > 0.035
        return self.parser.feed(text, flush=flush)

    def stop_input(self):
        """Stop terminal reports before potentially slow overlay teardown."""
        if not self._input_stopped:
            keyboard = "\x1b[<u" if self._keyboard_pushed else ""
            self.write("\x1b[?2026l" + keyboard + "\x1b[?1000l\x1b[?1002l\x1b[?1003l"
                       "\x1b[?1006l\x1b[?2004l")
            self._keyboard_pushed = False
            self._input_stopped = True

    def _discard_input(self):
        if os.name == "nt":
            import msvcrt
            consumed = False
            # Read through the same CRT path as read(): a console-only flush
            # would leave any characters already buffered by the CRT behind.
            for _ in range(4096):
                if not msvcrt.kbhit():
                    break
                msvcrt.getwch()
                consumed = True
            return consumed
        import select
        if select.select([self.input], [], [], 0)[0]:
            return bool(os.read(self.input.fileno(), 65536))
        return False

    def _drain_input(self):
        # Output flush does not mean the terminal/ConPTY has already processed
        # mouse-disable sequences. Allow in-flight reports to arrive while we
        # still own raw input; never dispatch them to widgets during shutdown.
        deadline = time.monotonic() + .1
        quiet_until = time.monotonic() + .05
        try:
            while True:
                consumed = self._discard_input()
                now = time.monotonic()
                if consumed:
                    quiet_until = now + .05
                if now >= min(quiet_until, deadline):
                    break
                time.sleep(min(.002, quiet_until - now, deadline - now))
        except (OSError, ValueError):
            # A disconnected terminal must not prevent restoration of modes.
            pass
        finally:
            self.parser = InputParser()
            self._decoder.reset()
            self._wide_decoder.reset()
            self._special = False

    def __exit__(self, exc_type, exc, tb):
        try:
            try:
                self.stop_input()
            finally:
                try:
                    if self._input_active:
                        self._drain_input()
                finally:
                    self.write("\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
        finally:
            self._input_active = False
            for restore in reversed(self._restore):
                restore()
            self._restore.clear()
