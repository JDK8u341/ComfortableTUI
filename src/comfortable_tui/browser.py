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
from .gpu import GpuFrameSource, gpu_available
from .security import BrowserLimits, BrowserPolicy, SECURITY_PROTOCOL, origin_names, topic_names, url_text


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


class _BrowserGpuSource(GpuFrameSource):
    """Keep the native provider alive until all display consumers acknowledge it."""
    def __init__(self, service, view, name):
        attached = GpuFrameSource.attach(name)
        super().__init__(attached.info)
        self._service, self._view = service, view

    def close(self):
        if not self._closed and not self._service._closed:
            self._service._rpc.request("release_gpu_frame", view=self._view, name=self.info.name)
        super().close()


class BrowserService:
    """One application-private browser runtime with explicit storage and message scopes.

    The executable is an optional, separately built CEF host. For persistent profiles,
    supply a stable application_id. Put App's context inside this service's context:
    display consumers must detach before the service frees its frame mappings.
    """
    def __init__(self, application_id=None, *, executable=None, data_root=None,
                 display_platform=None, allow_no_sandbox=False, timeout=30, policy=None, limits=None):
        if allow_no_sandbox:
            raise PermissionError("Disabling the browser sandbox is no longer supported")
        self.policy = policy if policy is not None else BrowserPolicy()
        self.limits = limits if limits is not None else BrowserLimits()
        if not isinstance(self.policy, BrowserPolicy) or not isinstance(self.limits, BrowserLimits):
            raise TypeError("Expected BrowserPolicy and BrowserLimits")
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
            self._rpc = NativeProcess(command, ready_event="service_ready", log_path=self.root / "service-stderr.log",
                                      timeout=timeout, env=display_environment(display_platform))
            version = self._rpc.capabilities.get("security_protocol")
            if type(version) is not int or version != SECURITY_PROTOCOL:
                self._rpc.close()
                raise RuntimeError("Browser helper lacks security protocol 2; rebuild its CEF runtime")
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
        if name not in self._profiles and len(self._profiles) >= 64:
            raise PermissionError("At most 64 browser profiles per service")
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
             publish_topics=(), subscribe_topics=(), message_group=None, policy=None, render_mode="cpu"):
        if self._closed:
            raise RuntimeError("BrowserService is closed")
        if len(self._pages) >= self.limits.max_pages:
            raise PermissionError("Browser page budget exhausted (includes pinned retired pages)")
        if render_mode not in ("cpu", "gpu"):
            raise ValueError("render_mode must be cpu or gpu")
        if render_mode == "gpu" and (not gpu_available() or self.capabilities.get("gpu_texture_transport") != 1):
            raise RuntimeError("WebView GPU mode requires Windows GPU extension and a matching GPU-capable CEF helper")
        policy = self.policy if policy is None else policy
        if not isinstance(policy, BrowserPolicy):
            raise TypeError("Expected BrowserPolicy")
        url, origins = url_text(url), origin_names(origins)
        wire = dict(asdict(policy), protocol=SECURITY_PROTOCOL)
        publish_topics, subscribe_topics = topic_names(publish_topics), topic_names(subscribe_topics)
        self._check_frame_budget(width, height, render_mode)
        if type(fps) is not int or not 1 <= fps <= 240:
            raise ValueError("fps must be 1..240")
        profile = self._profile(profile)
        source = FrameBuffer(width, height, pixel_format="bgra8_premultiplied") if render_mode == "cpu" else None
        page = BrowserPage(self, profile, source, message_group)
        page.policy, page.render_mode = policy, render_mode
        try:
            page.info = self._rpc.request("open", view=page.id, profile=_key(profile.name),
                persistent=profile.persistent, persist_session_cookies=profile.persist_session_cookies,
                scope=page.scope, source=(asdict(source.info) if source else
                    dict(width=width, height=height, stride=width * 4, pixel_format="bgra8_premultiplied")),
                render_mode=render_mode, fps=fps, url=str(url), origins=list(origins),
                publish_topics=list(publish_topics), subscribe_topics=list(subscribe_topics), security=wire,
                limits=asdict(self.limits))
            page._security = page.info["security"]
            if render_mode == "gpu":
                if page.info.get("frame_backend") != "native_gpu_d3d11":
                    raise RuntimeError("CEF helper did not negotiate strict GPU texture transport")
                source = _BrowserGpuSource(self, page.id, page.info["source"]["name"])
                page.source = source
                page._buffers.append(source)
            self._pages[page.id] = page
            return page
        except BaseException:
            if render_mode == "gpu":
                try:
                    self._rpc.request("close", view=page.id)
                except Exception:
                    pass  # Preserve the original negotiation/attachment error.
                try:
                    self._rpc.request("release_gpu_page", view=page.id)
                except Exception:
                    pass
                if source:
                    source._closed = True  # Native close/release above owns rollback.
            elif source:
                source.close()
            raise

    def publish(self, topic, payload, *, profile=None, message_group=None, target=""):
        topic_names((topic,))
        profile = self._profile(profile)
        scope = self._scope(profile, message_group)
        return self._rpc.request("publish", scope=scope, topic=str(topic), payload=payload, target=target)

    def _check_frame_budget(self, width, height, render_mode="cpu"):
        requested = self.limits.frame_bytes(width, height)
        if render_mode == "gpu":
            requested = 64 + width * height * 4 * 5
            if requested > self.limits.max_frame_bytes:
                raise PermissionError("Browser GPU surfaces exceed the per-page buffer budget")
        current = sum(64 + b.info.width * b.info.height * 4 *
                      (5 if isinstance(b, GpuFrameSource) else 3) for p in self._pages.values()
                      for b in p._buffers if not b._closed)
        if current + requested > self.limits.max_total_frame_bytes:
            raise PermissionError("Browser total buffer budget exhausted (includes retired frames)")

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
        self._buffers, self._widgets, self._listeners = ([source] if source else []), weakref.WeakSet(), []
        self._attachments = {}  # Consumer token -> acknowledged descriptor, or None while attaching.
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
        result = self._request("navigate", url=url_text(url))
        self.loaded = False
        return result

    def reload(self):
        return self._request("reload")

    def evaluate_async(self, code):
        if not self.policy.allow_evaluate:
            raise PermissionError("JavaScript evaluation requires explicit BrowserPolicy(allow_evaluate=True)")
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

    def set_muted(self, muted):
        """Explicit host audio control; ordinary Chromium playback rules still apply."""
        return self._request("muted", muted=bool(muted))

    def frame_stats(self):
        """Native frame counters; diagnostic RPC, never needed for frame delivery."""
        return self._request("frame_stats")

    def resize(self, width, height):
        if self._closed:
            raise RuntimeError("BrowserPage is closed")
        self.service._check_frame_budget(width, height, self.render_mode)
        if self.render_mode == "gpu":
            result = self._request("resize", source=dict(width=width, height=height, stride=width * 4,
                                                        pixel_format="bgra8_premultiplied"))
            try:
                source = _BrowserGpuSource(self.service, self.id, result["source"]["name"])
            except BaseException:
                # The helper has committed a new channel. Stop this page before
                # releasing that unattachable channel; old displayed frames stay pinned.
                self.close()
                self.service._rpc.request("release_gpu_frame", view=self.id, name=result["source"]["name"])
                raise
        else:
            source = FrameBuffer(width, height, pixel_format="bgra8_premultiplied")
        try:
            if self.render_mode == "cpu":
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
        if self._closed and not self._widgets and not self._attachments:
            for buffer in self._buffers:
                buffer.close()
            self._buffers.clear()
            self.service._pages.pop(self.id, None)
            return
        if any(info is None for info in self._attachments.values()):
            return
        pinned = set(self._attachments.values())
        keep = []
        for buffer in self._buffers:
            if buffer is self.source or buffer.info in pinned:
                keep.append(buffer)
            else:
                buffer.close()
        self._buffers = keep

    def _display_buffer(self, token, info):
        self._attachments[token] = info
        self._collect_buffers()

    def _display_detached(self, token):
        self._attachments.pop(token, None)
        self._collect_buffers()

    def input(self, kind, **data):
        if not self._closed:
            return self.service._rpc.send("input", view=self.id, kind=kind, data=data)

    def close(self):
        if self._closed:
            return
        self._request("close")
        self._closed = True
        self._collect_buffers()
