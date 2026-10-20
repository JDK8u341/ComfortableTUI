"""Windows host discovery and UIA text calibration (no console HWND guesses)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import threading
import time
import uuid

from .overlay import PixelRect, TerminalHost


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class Win32:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                                 wintypes.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        for name, args, result in (
            ("GetForegroundWindow", [], wintypes.HWND),
            ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("GetWindowLongPtrW", [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            ("IsWindow", [wintypes.HWND], wintypes.BOOL),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
            ("SetFocus", [wintypes.HWND], wintypes.HWND),
            ("GetDpiForWindow", [wintypes.HWND], wintypes.UINT),
            ("SetThreadDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_void_p),
            ("MonitorFromRect", [ctypes.POINTER(wintypes.RECT), wintypes.DWORD], wintypes.HANDLE),
            ("GetMonitorInfoW", [wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.GetConsoleWindow.restype = wintypes.HWND
        self.user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    def class_name(self, hwnd):
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, len(name))
        return name.value

    def origin(self, hwnd):
        point = wintypes.POINT()
        if not self.user.ClientToScreen(hwnd, ctypes.byref(point)):
            raise OSError("terminal client coordinates unavailable")
        return point.x, point.y

    def own(self, hwnd, owner):
        ctypes.set_last_error(0)
        result = self.user.SetWindowLongPtrW(hwnd, -8, owner)  # GWLP_HWNDPARENT, not SetParent.
        if not result and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())

    def monitor(self, rect):
        native = wintypes.RECT(round(rect.x), round(rect.y), round(rect.x + rect.width),
                               round(rect.y + rect.height))
        handle = self.user.MonitorFromRect(ctypes.byref(native), 2)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise OSError("could not locate overlay monitor")
        return info.szDevice, info.rcMonitor.left, info.rcMonitor.top

    def keep_above_owner(self, hwnd, owner):
        # Qt can recreate/reset an HWND while showing or changing DPI. Repair
        # ownership and, only in our foreground group, restore group z-order.
        if self.user.GetWindow(hwnd, 4) != owner:  # GW_OWNER
            self.own(hwnd, owner)
        foreground = self.user.GetForegroundWindow()
        if foreground in (hwnd, owner) or self.user.GetAncestor(foreground, 3) == owner:
            current = self.user.GetWindow(owner, 3)  # GW_HWNDPREV: windows above owner.
            for _ in range(512):
                if not current or current == hwnd:
                    break
                current = self.user.GetWindow(current, 3)
            if current != hwnd:
                self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                       0x0001 | 0x0002 | 0x0010 | 0x0200)  # HWND_TOP, no activation.
            if foreground == hwnd:
                # Activating a WS_EX_TOOLWINDOW can raise only the tool, leaving
                # its owner behind unrelated apps. Raise the *group*, not a
                # globally topmost overlay floating over another application.
                below = self.user.GetWindow(hwnd, 2)
                for _ in range(512):
                    if not below or (self.user.IsWindowVisible(below) and self.class_name(below) not in (
                            "IME", "MSCTFIME UI", "PseudoConsoleWindow")):
                        break
                    below = self.user.GetWindow(below, 2)
                if below != owner:
                    self.user.SetWindowPos(owner, hwnd, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200)
        else:
            # Stay immediately above the background terminal, below whatever
            # covers it. Showing an inactive Qt tool must not raise it over the
            # foreground application, and never activates either group member.
            above = self.user.GetWindow(owner, 3)
            if above != hwnd:
                get_style = getattr(self.user, "GetWindowLongPtrW", None)
                if above and get_style and get_style(above, -20) & 0x8:  # WS_EX_TOPMOST
                    above = None  # Top of the normal band; never join the topmost band.
                self.user.SetWindowPos(hwnd, above, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200)

    def cloaked(self, hwnd):
        value = wintypes.DWORD()
        return self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value)) == 0 and bool(value.value)


_TERMINALS = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass", "org.wezfurlong.wezterm", "WezTermWindow"}


def _marker_host(marker, timeout=3):
    """Bind to this VT stream, independently of foreground activation races."""
    result, finished = [], threading.Event()

    def discover():
        import sys
        already_imported = "comtypes" in sys.modules
        sys.coinit_flags = 0
        initialized = False
        try:
            import comtypes
            if already_imported:
                comtypes.CoInitializeEx(0)
            initialized = True
            import comtypes.client
            uia = comtypes.client.GetModule("UIAutomationCore.dll")
            automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            api = Win32()
            api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                handles = []

                def collect(hwnd, _):
                    if api.class_name(hwnd) in _TERMINALS and api.user.IsWindowVisible(hwnd):
                        handles.append(hwnd)
                    return True

                api.user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
                api.user.EnumWindows(callback_type(collect), 0)
                for hwnd in handles:
                    try:
                        root = automation.ElementFromHandle(hwnd)
                        elements = root.FindAll(uia.TreeScope_Subtree, automation.CreatePropertyCondition(
                            uia.UIA_IsTextPatternAvailablePropertyId, True))
                        candidates = _terminal_text_candidates([elements.GetElement(i) for i in range(elements.Length)])
                        for index, element in enumerate(candidates):
                            pattern = element.GetCurrentPattern(uia.UIA_TextPatternId).QueryInterface(
                                uia.IUIAutomationTextPattern)
                            if pattern.DocumentRange.FindText(marker, False, False):
                                result.append((int(hwnd), index, tuple(element.GetRuntimeId())))
                                return
                    except Exception:
                        continue  # An unrelated terminal can close during enumeration.
                finished.wait(.05)
        except Exception:
            pass
        finally:
            if initialized:
                comtypes.CoUninitialize()
            finished.set()

    thread = threading.Thread(target=discover, name="tui-bind-host", daemon=True)
    thread.start()
    finished.wait(timeout + .25)
    return result[0] if result else None


def resolve_host(host: TerminalHost, *, terminal=None, style=None):
    api = Win32()
    hwnd = host.hwnd
    pane_index = host.pane_index
    runtime_id = host._runtime_id
    if hwnd is None:
        # GetConsoleWindow can be visible yet belong to PseudoConsoleWindow.
        # Foreground alone can be another app/terminal after spawn or activation.
        if terminal is not None:
            width, _ = terminal.size
            marker = ("ctui" + uuid.uuid4().hex)[:min(24, max(1, width - 1))]
            if style is not None:
                ansi = style.with_(fg=style.bg).ansi()
            else:
                ansi = "\x1b[38;2;17;21;34m\x1b[48;2;17;21;34m"
            terminal.write("\x1b7\x1b[1;1H" + ansi + marker + "\x1b[0m\x1b8")
            match = _marker_host(marker)
            if match:
                hwnd, detected_pane, detected_runtime = match
                if pane_index is None:
                    pane_index = detected_pane
                    runtime_id = detected_runtime
            elif host.viewport is None:
                raise RuntimeError("Cannot bind this terminal output stream to a visible UIA text provider. "
                                   "Supply TerminalHost(hwnd=..., viewport=PixelRect(...)) for hosts without UIA.")
        if hwnd is not None:
            return TerminalHost(hwnd, host.viewport, pane_index, host.follow_resize, runtime_id)
        foreground = api.user.GetForegroundWindow()
        root = api.user.GetAncestor(foreground, 3)
        if api.class_name(foreground) in _TERMINALS:
            hwnd = foreground
        elif root and api.class_name(root) in _TERMINALS:
            hwnd = root
        else:
            console = api.kernel.GetConsoleWindow()
            if console and api.user.IsWindowVisible(console) and api.class_name(console) == "ConsoleWindowClass":
                hwnd = console
            else:
                raise RuntimeError("Cannot identify the terminal host. Start in the focused terminal, "
                                   "or supply TerminalHost(hwnd=..., viewport=PixelRect(...)).")
    if not api.user.IsWindow(hwnd):
        raise RuntimeError("TerminalHost hwnd is not a live window")
    return TerminalHost(int(hwnd), host.viewport, pane_index, host.follow_resize, runtime_id)


@dataclass(frozen=True)
class HostSnapshot:
    viewport: PixelRect | None
    available: bool
    error: str = ""


def _terminal_text_candidates(elements):
    visible = [element for element in elements if not element.CurrentIsOffscreen]
    controls = [element for element in visible if element.CurrentClassName == "TermControl"]
    return controls or [element for element in visible if element.CurrentControlType in (50004, 50030)
                        or element.CurrentClassName == "ConsoleWindowClass"]


class HostTracker:
    """Slow COM calls stay on a dedicated thread; movement uses cached offsets.

    All native/COM coordinates are physical pixels in a per-monitor-aware thread.
    An explicit viewport bypasses UIA and tracks the owner's client origin.
    """

    def __init__(self, host, grid, *, visibility_only=False):
        self.host, self.grid = host, grid
        self.visibility_only = visibility_only
        self.api = Win32()
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._snapshot = HostSnapshot(host.viewport, host.viewport is not None)
        client = wintypes.RECT()
        self.api.user.GetClientRect(host.hwnd, ctypes.byref(client))
        self._client_size = (client.right, client.bottom)
        self._geometry = None
        self._geometry_time = time.perf_counter()
        self._thread = None
        if host.viewport is None:
            self._thread = threading.Thread(target=self._track, name="tui-viewport-uia", daemon=True)
            self._thread.start()

    def _track(self):
        import sys
        # comtypes initializes the importing thread on its first import.
        already_imported = "comtypes" in sys.modules
        sys.coinit_flags = 0
        initialized = False
        try:
            import comtypes
            if already_imported:
                comtypes.CoInitializeEx(0)
            initialized = True
            import comtypes.client
            self.api.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            uia = comtypes.client.GetModule("UIAutomationCore.dll")
            automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            root = automation.ElementFromHandle(self.host.hwnd)
            condition = automation.CreatePropertyCondition(uia.UIA_IsTextPatternAvailablePropertyId, True)
            elements = root.FindAll(uia.TreeScope_Subtree, condition)
            # XAML tab titles also implement TextPattern. They are not terminals.
            candidates = _terminal_text_candidates([elements.GetElement(i) for i in range(elements.Length)])
            if not candidates:
                raise RuntimeError("No terminal text provider; supply TerminalHost(viewport=PixelRect(...))")
            if self.host._runtime_id is not None:
                matches = [element for element in candidates
                           if tuple(element.GetRuntimeId()) == self.host._runtime_id]
                if not matches:
                    raise RuntimeError("Bound terminal pane is unavailable; refusing to attach a different pane")
                element = matches[0]
            elif self.host.pane_index is not None:
                element = candidates[self.host.pane_index]
            else:
                focused = [e for e in candidates if e.CurrentHasKeyboardFocus]
                if not focused:
                    current = automation.GetFocusedElement()
                    while current:
                        focused = [e for e in candidates if automation.CompareElements(e, current)]
                        if focused:
                            break
                        current = automation.ControlViewWalker.GetParentElement(current)
                if not focused and len(candidates) != 1:
                    raise RuntimeError("Ambiguous terminal text panes; focus the desired pane or set pane_index")
                element = focused[0] if focused else candidates[0]
            # Pin the element, never silently switch to a different terminal tab/pane.
            pattern = None if self.visibility_only else element.GetCurrentPattern(
                uia.UIA_TextPatternId).QueryInterface(uia.IUIAutomationTextPattern)
            while not self._stop.is_set():
                try:
                    if self.visibility_only:
                        with self._lock:
                            self._snapshot = HostSnapshot(None, not element.CurrentIsOffscreen)
                        self._stop.wait(.1)
                        continue
                    if element.CurrentIsOffscreen:
                        snapshot = HostSnapshot(None, False)
                    else:
                        geometry = self._sample_geometry()
                        # UIA returns screen coordinates via several independent
                        # RPCs. Never turn a moving/stale screen sample into a
                        # client offset. Keep following the last calibrated offset.
                        if not self._geometry_settled(geometry):
                            self._stop.wait(0.02)
                            continue
                        grid = self.grid
                        ranges = pattern.GetVisibleRanges()
                        if not ranges.Length:
                            raise RuntimeError("terminal has no visible text range")
                        text = ranges.GetElement(0)
                        rectangles = tuple(text.GetBoundingRectangles())
                        character = text.Clone()
                        character.MoveEndpointByRange(uia.TextPatternRangeEndpoint_End, text,
                                                      uia.TextPatternRangeEndpoint_Start)
                        character.MoveEndpointByUnit(uia.TextPatternRangeEndpoint_End, uia.TextUnit_Character, 1)
                        cell = tuple(character.GetBoundingRectangles())
                        if len(cell) < 4 or len(rectangles) < 4 or min(cell[2:4]) <= 0:
                            raise RuntimeError("cannot measure terminal cells; supply an explicit viewport")
                        # A terminal range starts at the visible row's first column.
                        # A wide first glyph spans two columns: bound by the row width.
                        columns, rows = grid
                        cell_width = min(cell[2], rectangles[2] / max(1, columns))
                        ox, oy = geometry[:2]
                        viewport = PixelRect(rectangles[0] - ox, rectangles[1] - oy,
                                             cell_width * columns, cell[3] * rows)
                        bounds = element.CurrentBoundingRectangle
                        if viewport.width > bounds.right - bounds.left + cell_width or (
                                viewport.height > bounds.bottom - bounds.top + cell[3]):
                            raise RuntimeError("terminal grid is resizing; waiting for matching text geometry")
                        if grid != self.grid or not self._geometry_settled(geometry):
                            continue  # The host moved/resized while COM was reading.
                        snapshot = HostSnapshot(viewport, True)
                    with self._lock:
                        self._snapshot = snapshot
                except Exception as exc:
                    with self._lock:
                        self._snapshot = HostSnapshot(None, False, str(exc))
                self._stop.wait(0.1)
        except Exception as exc:
            with self._lock:
                self._snapshot = HostSnapshot(None, False, str(exc))
        finally:
            if initialized:
                comtypes.CoUninitialize()

    def _sample_geometry(self):
        x, y = self.api.origin(self.host.hwnd)
        client = wintypes.RECT()
        self.api.user.GetClientRect(self.host.hwnd, ctypes.byref(client))
        geometry = (x, y, client.right, client.bottom, self.api.user.GetDpiForWindow(self.host.hwnd))
        with self._lock:
            if geometry != self._geometry:
                self._geometry = geometry
                self._geometry_time = time.perf_counter()
        return geometry

    def _geometry_settled(self, expected):
        current = self._sample_geometry()
        with self._lock:
            return current == expected and time.perf_counter() - self._geometry_time >= 0.15

    def snapshot(self):
        x, y, *_ = self._sample_geometry()
        with self._lock:
            snapshot = self._snapshot
        if snapshot.viewport is None:
            return snapshot
        rect = snapshot.viewport
        if self.host.viewport is not None and self.host.follow_resize:
            client = wintypes.RECT()
            self.api.user.GetClientRect(self.host.hwnd, ctypes.byref(client))
            rect = PixelRect(rect.x, rect.y, max(0, rect.width + client.right - self._client_size[0]),
                             max(0, rect.height + client.bottom - self._client_size[1]))
        return HostSnapshot(PixelRect(x + rect.x, y + rect.y, rect.width, rect.height), snapshot.available,
                            snapshot.error)

    def visible(self, overlay_hwnd):
        user, hwnd = self.api.user, self.host.hwnd
        return bool(user.IsWindow(hwnd) and user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd)
                    and not getattr(self.api, "cloaked", lambda handle: False)(hwnd))

    def request_close(self):
        """Wake the tracker before waiting for other independent workers."""
        self._stop.set()

    def close(self):
        self.request_close()
        if self._thread:
            self._thread.join(timeout=0.5)


class HostLocationHook:
    """Owner-only asynchronous WinEvent hook, dispatched by Qt's message loop.

    The callback only relocates an already configured layer. No COM, scene
    handshake, injection, global mouse hook, or additional worker is involved.
    The existing timer remains a fallback if registration is unavailable.
    """

    def __init__(self, api, hwnd, changed):
        self.user = api.user
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                           wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

        def dispatch(hook, event, window, object_id, child_id, thread, timestamp):
            if window == hwnd and object_id == 0 and child_id == 0:
                changed()

        self._callback = callback_type(dispatch)  # Retain until UnhookWinEvent.
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                             callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        self.user.SetWinEventHook.restype = wintypes.HANDLE
        self.user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        self.user.UnhookWinEvent.restype = wintypes.BOOL
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Never subscribe desktop-wide if the owner has already disappeared.
        self.handle = self.user.SetWinEventHook(0x800B, 0x800B, None, self._callback,
                                                pid.value, 0, 0) if pid.value else None

    def close(self):
        if self.handle:
            self.user.UnhookWinEvent(self.handle)
            self.handle = None
