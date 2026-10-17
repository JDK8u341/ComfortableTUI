"""Optional CEF service above the generic frame API; no pixel copying in Python."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from concurrent.futures import TimeoutError as FutureTimeoutError
import hashlib
import os
from pathlib import Path
import tempfile
import uuid
import weakref

from ._native_process import NativeProcess
from ._display_environment import display_environment
from ._shared_frame import FrameBuffer


def _key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class _ProfileLock:
    def __init__(self, root):
        self.file = (root / ".ctui-service.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("This application's browser data is already in use; reuse its BrowserService "
                               "or select a separate data_root") from None

    def close(self):
        self.file.close()


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    persistent: bool = False
    persist_session_cookies: bool = False
    _owner: str = ""


class BrowserService:
    """One application-private browser runtime with explicit storage and message scopes.

    The executable is an optional, separately built CEF host. For persistent profiles,
    supply a stable application_id. Put App's context inside this service's context:
    display consumers must detach before the service frees its frame mappings.
    """
    def __init__(self, application_id=None, *, executable=None, data_root=None,
                 display_platform=None, allow_no_sandbox=False, timeout=30):
        if application_id is not None and (not isinstance(application_id, str) or not application_id.strip()):
            raise ValueError("application_id must be a nonempty stable string")
        if data_root is not None and application_id is None:
            raise ValueError("data_root requires application_id")
        self.application_id = application_id
        self._token = uuid.uuid4().hex
        self._temporary = None
        if application_id is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="ctui-browser-")
            self.root = Path(self._temporary.name)
        else:
            default = (Path(os.environ.get("LOCALAPPDATA", Path.home())) if os.name == "nt" else
                       Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
            self.root = (Path(data_root) if data_root else default / "ComfortableTUI/browser") / _key(application_id)
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = _ProfileLock(self.root)
        self._profiles, self._pages, self._listeners = {}, {}, []
        self._app_bindings = weakref.WeakKeyDictionary()
        self._closed = False
        if executable is None:
            executable = os.environ.get("COMFORTABLE_TUI_BROWSER_EXECUTABLE")
        if executable is None:
            build = Path(__file__).resolve().parents[2] / ".research/browser-build/native/browser"
            executable = build / ("Release/ctui_browser.exe" if os.name == "nt" else "ctui_browser")
        self.executable = Path(executable).resolve()
        try:
            if not self.executable.is_file():
                raise RuntimeError("CEF service is not built; set COMFORTABLE_TUI_BROWSER_EXECUTABLE "
                                   "or build with COMFORTABLE_TUI_BUILD_BROWSER=ON; see docs/webview.md")
            # Set these at launch as well as in the native entry point: startup
            # checks must never open Chrome onboarding before the service handshake.
            command = [str(self.executable), f"--ctui-root={self.root}",
                       "--no-first-run", "--no-default-browser-check"]
            if display_platform:
                if display_platform not in ("x11", "wayland") or os.name == "nt":
                    raise ValueError("display_platform must be x11/wayland on Linux")
                command.append(f"--ozone-platform={display_platform}")
            if allow_no_sandbox:
                command.extend(("--ctui-allow-no-sandbox", "--no-sandbox"))
            self._rpc = NativeProcess(command, ready_event="service_ready", log_path=self.root / "service-stderr.log",
                                      timeout=timeout, env=display_environment(display_platform))
            self.capabilities = dict(self._rpc.capabilities, application_isolated=True,
                                     storage_backend="cef_request_context", messaging="cef_router")
        except BaseException:
            self._lock.close()
            if self._temporary:
                self._temporary.cleanup()
            raise

    def profile(self, name="main", *, persistent=False, persist_session_cookies=False):
        if not isinstance(name, str) or not name:
            raise ValueError("profile name must be nonempty")
        if persistent and self.application_id is None:
            raise ValueError("Persistent profiles require a stable application_id")
        if persist_session_cookies and not persistent:
            raise ValueError("Session-cookie persistence requires a persistent profile")
        result = BrowserProfile(name, bool(persistent), bool(persist_session_cookies), self._token)
        existing = self._profiles.setdefault(name, result)
        if existing != result:
            raise ValueError("Profile settings cannot change during a service session")
        return existing

    def _profile(self, profile):
        profile = profile or self.profile()
        if profile._owner != self._token:
            raise ValueError("Profile belongs to a different BrowserService")
        return profile

    def open(self, url, *, profile=None, width=960, height=540, fps=60, origins=(),
             publish_topics=(), subscribe_topics=(), message_group=None):
        if self._closed:
            raise RuntimeError("BrowserService is closed")
        if sum(not page._closed for page in self._pages.values()) >= 64:
            raise RuntimeError("BrowserService supports at most 64 active pages")
        if not isinstance(fps, int) or not 1 <= fps <= 240:
            raise ValueError("fps must be 1..240")
        profile = self._profile(profile)
        source = FrameBuffer(width, height, pixel_format="bgra8_premultiplied")
        page = BrowserPage(self, profile, source, message_group)
        try:
            page.info = self._rpc.request("open", view=page.id, profile=_key(profile.name),
                persistent=profile.persistent, persist_session_cookies=profile.persist_session_cookies,
                scope=page.scope, source=asdict(source.info), fps=fps, url=str(url), origins=list(origins),
                publish_topics=list(publish_topics), subscribe_topics=list(subscribe_topics))
            self._pages[page.id] = page
            return page
        except BaseException:
            source.close()
            raise

    def publish(self, topic, payload, *, profile=None, message_group=None, target=""):
        profile = self._profile(profile)
        scope = self._scope(profile, message_group)
        return self._rpc.request("publish", scope=scope, topic=str(topic), payload=payload, target=target)

    def _scope(self, profile, group):
        # Groups are explicitly assigned by the app, never accepted from webpage payloads.
        return _key(self._token + ("group:" + str(group) if group is not None else "profile:" + profile.name))

    def on_event(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def poll(self):
        if self._closed:
            return []
        events = self._rpc.events()
        for event in events:
            page = self._pages.get(event.get("view"))
            if page:
                page._event(event)
            for callback in tuple(self._listeners):
                callback(event)
        return events

    def _bind_app(self, app):
        binding = self._app_bindings.get(app)
        if binding is None:
            binding = [0, app.watch_events(self.poll)]
            self._app_bindings[app] = binding
        binding[0] += 1
        stopped = False
        def stop():
            nonlocal stopped
            if stopped:
                return
            stopped = True
            binding[0] -= 1
            if binding[0] == 0:
                binding[1]()
                self._app_bindings.pop(app, None)
        return stop

    def close(self):
        if self._closed:
            return
        self._closed = True
        for _, stop in tuple(self._app_bindings.values()):
            stop()
        self._app_bindings.clear()
        try:
            self._rpc.close()
        finally:
            for page in self._pages.values():
                page._closed = True
                for buffer in page._buffers:
                    buffer.close()
            self._pages.clear()
            self._lock.close()
            if self._temporary:
                self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class BrowserPage:
    def __init__(self, service, profile, source, group):
        self.service, self.profile, self.source = service, profile, source
        self.id, self.scope = uuid.uuid4().hex, service._scope(profile, group)
        self._buffers, self._widgets, self._listeners = [source], weakref.WeakSet(), []
        self._closed = False
        self.loaded, self.url, self.title, self.info = False, "", "", {}

    def _event(self, event):
        data = event.get("data", {})
        if event["event"] == "loaded":
            self.loaded, self.url = True, data.get("url", "")
        elif event["event"] == "navigating":
            self.loaded = False
        elif event["event"] == "title":
            self.title = data.get("title", "")
        for callback in tuple(self._listeners):
            callback(event)

    def on_event(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _request(self, op, **fields):
        if self._closed:
            raise RuntimeError("BrowserPage is closed")
        return self.service._rpc.request(op, view=self.id, **fields)

    def navigate(self, url):
        self.loaded = False
        return self._request("navigate", url=str(url))

    def reload(self):
        return self._request("reload")

    def evaluate_async(self, code):
        if self._closed:
            raise RuntimeError("BrowserPage is closed")
        return self.service._rpc.send("eval", view=self.id, code=str(code))

    def evaluate(self, code, *, timeout=15):
        future = self.evaluate_async(code)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            self.service._rpc.send("cancel_eval", view=self.id, request=future._native_request)
            raise TimeoutError("Browser evaluation timed out") from None
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result.get("value")

    def set_visible(self, visible):
        return self._request("visible", visible=bool(visible))

    def resize(self, width, height):
        source = FrameBuffer(width, height, pixel_format="bgra8_premultiplied")
        try:
            self._request("resize", source=asdict(source.info))
        except BaseException:
            source.close()
            raise
        self._buffers.append(source)
        self.source = source
        for widget in tuple(self._widgets):
            widget.set_buffer(source)
        self._collect_buffers()

    def _collect_buffers(self):
        # Attach/detach is acknowledged by the display consumer, not inferred
        # from a Python layout change. Preserve every buffer a consumer may use.
        if self._closed and not self._widgets:
            for buffer in self._buffers:
                buffer.close()
            self._buffers.clear()
            self.service._pages.pop(self.id, None)
            return
        if any(w._session is not None and getattr(w, "_browser_attached_source", None) is None
               for w in self._widgets):
            return
        pinned = {w._browser_attached_source for w in self._widgets
                  if getattr(w, "_browser_attached_source", None) is not None}
        keep = []
        for buffer in self._buffers:
            if buffer is self.source or buffer.info in pinned:
                keep.append(buffer)
            else:
                buffer.close()
        self._buffers = keep

    def input(self, kind, **data):
        if not self._closed:
            return self.service._rpc.send("input", view=self.id, kind=kind, data=data)

    def close(self):
        if self._closed:
            return
        self._request("close")
        self._closed = True
        self._collect_buffers()
