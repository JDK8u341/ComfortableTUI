"""Adversarial boundary cases, not a substitute for OS sandbox verification."""
from dataclasses import FrozenInstanceError, replace
import os
import struct
import sys

import pytest

from comfortable_tui import BrowserLimits, BrowserPolicy, BrowserService, FrameBuffer, FrameBufferInfo, WebView
from comfortable_tui import browser
from comfortable_tui._native_process import NativeProcess
from comfortable_tui.security import url_text
from test_browser import FakeProcess


@pytest.mark.parametrize("value", [None, 1, True, "", "x" * 8193], ids=["none", "integer", "boolean", "empty", "too-long"])
def test_python_checks_url_shape_without_a_second_parser(value):
    with pytest.raises(ValueError):
        url_text(value)


def test_policy_is_an_immutable_native_configuration():
    policy = BrowserPolicy()
    assert not policy.allow_evaluate and policy.navigation_origins == ()
    with pytest.raises(FrozenInstanceError):
        policy.allow_evaluate = True
    with pytest.raises(ValueError):
        BrowserPolicy(navigation_origins="https://example.com")
    # Keep spelling intact until CEF canonicalizes it. No Python IDNA interpretation.
    assert BrowserPolicy(navigation_origins=["https://faß.de"]).navigation_origins == ("https://faß.de",)


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "NativeProcess", FakeProcess)
    executable = tmp_path / "helper"
    executable.touch()
    with BrowserService(executable=executable, limits=BrowserLimits(max_frame_bytes=3000, max_total_frame_bytes=3000)) as value:
        yield value


def test_host_page_settings_override_service_defaults_without_a_permission_hierarchy(service):
    page = service.open("https://example.com", origins=["https://example.com"], width=8, height=8)
    with pytest.raises(PermissionError):
        page.evaluate_async("42")
    page.navigate("https://EXAMPLE.com:443/next")
    assert service._rpc.messages[-1][1]["url"] == "https://EXAMPLE.com:443/next"
    other = service.open("https://example.com", width=8, height=8, policy=BrowserPolicy(allow_evaluate=True))
    other.evaluate_async("42")
    assert service._rpc.messages[-1][0] == "eval"
    before = len(service._rpc.messages)
    with pytest.raises(ValueError):
        service.open("https://example.com", publish_topics=["*"], width=8, height=8)
    assert len(service._rpc.messages) == before


def test_partial_frame_setup_releases_created_mapping_and_locks(monkeypatch):
    from comfortable_tui import _shared_frame
    original = _shared_frame._NamedLock
    created = []

    def fail_second(name):
        created.append(name)
        if len(created) == 2:
            raise PermissionError("denied lock")
        return original(name)

    monkeypatch.setattr(_shared_frame, "_NamedLock", fail_second)
    with pytest.raises(PermissionError):
        FrameBuffer(2, 2)
    mapping_name = created[0].removesuffix("_meta")
    with pytest.raises(FileNotFoundError):
        _shared_frame.shared_memory.SharedMemory(name=mapping_name)


def test_frame_budget_includes_retired_buffers_pinned_by_consumer(service):
    from comfortable_tui import CanvasEvent
    page = service.open("about:blank", width=8, height=8)
    view = WebView(page)
    view._session = object()
    view._emit(CanvasEvent("attaching"))
    page.resize(12, 12)  # 832 old bytes + 1792 current bytes.
    with pytest.raises(PermissionError, match="total buffer"):
        page.resize(8, 8)
    assert len(page._buffers) == 2
    view.close()


@pytest.mark.parametrize("version", [None, True, "2", 1, 3])
def test_stale_helper_and_no_sandbox_fail_closed(monkeypatch, tmp_path, version):
    class Old(FakeProcess):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.capabilities.pop("security_protocol")
            if version is not None:
                self.capabilities["security_protocol"] = version
    monkeypatch.setattr(browser, "NativeProcess", Old)
    executable = tmp_path / "helper"
    executable.touch()
    with pytest.raises(RuntimeError, match="security protocol"):
        BrowserService(executable=executable)
    with pytest.raises(PermissionError, match="sandbox"):
        BrowserService(executable=executable, allow_no_sandbox=True)


@pytest.mark.parametrize("mutate", [lambda d: replace(d, name="../../secret"),
    lambda d: replace(d, name="Global\\mapping"), lambda d: replace(d, width=1.5),
    lambda d: replace(d, width=16385), lambda d: replace(d, stride=512 * 1024 * 1024)])
def test_invalid_descriptors_are_rejected_without_opening_mapping(monkeypatch, mutate):
    from comfortable_tui import _shared_frame
    monkeypatch.setattr(_shared_frame.shared_memory, "SharedMemory", lambda **kwargs: pytest.fail("OS mapping opened"))
    with pytest.raises(ValueError):
        FrameBuffer.attach(mutate(FrameBufferInfo("ctui_fixture", 4, 4, 16, "rgba8")))


@pytest.mark.parametrize("published,reading,timestamp", [(3, -1, 0), (-2, -1, 0),
    (0, 3, 0), (0, -1, float("inf")), (0, -1, float("nan"))])
def test_untrusted_frame_header_never_becomes_a_memory_slice(published, reading, timestamp):
    with FrameBuffer(2, 2) as buffer:
        struct.pack_into("<8siiQd", buffer._shm.buf, 0, b"CTUIFR01", published, reading, 1, timestamp)
        with pytest.raises(ValueError, match="header"):
            with buffer.read():
                pytest.fail("corrupt frame exposed")
        with pytest.raises(ValueError):
            _ = buffer.sequence


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock file")
def test_lock_symlink_does_not_open_or_modify_target(monkeypatch, tmp_path):
    from comfortable_tui import _shared_frame
    monkeypatch.setattr(_shared_frame.tempfile, "gettempdir", lambda: str(tmp_path))
    secret = tmp_path / "secret"
    secret.write_text("unchanged")
    (tmp_path / "ctui_test_meta.lock").symlink_to(secret)
    with pytest.raises(OSError):
        _shared_frame._NamedLock("ctui_test_meta")
    assert secret.read_text() == "unchanged"


@pytest.mark.parametrize("response", ['[]', '{"id":true,"ok":true}', '{"id":1,"ok":"yes"}',
    '{"event":"bad","value":NaN}', 'x' * (1024 * 1024 + 1)],
    ids=["array", "boolean-id", "string-ok", "nan", "oversized-no-newline"])
def test_malformed_or_unterminated_native_output_fails_pending_request(tmp_path, response):
    # Use a file, not a huge command line (Windows command lines are bounded).
    payload = tmp_path / "payload"
    payload.write_text(response, encoding="utf-8")
    code = ("import json,pathlib,sys; print(json.dumps({'event':'ready'}),flush=True);"
            "sys.stdin.readline();sys.stdout.write(pathlib.Path(sys.argv[1]).read_text());"
            "sys.stdout.flush();sys.stdin.read()")
    # Short malformed messages need a newline; the oversized attack deliberately has none.
    if len(response) < 1024:
        payload.write_text(response + "\n", encoding="utf-8")
    rpc = NativeProcess([sys.executable, "-u", "-c", code, str(payload)], ready_event="ready", log_path=tmp_path / "log")
    try:
        with pytest.raises(RuntimeError, match="connection closed"):
            rpc.send("probe").result(timeout=5)
    finally:
        rpc.close()


def test_native_stderr_is_drained_but_disk_usage_is_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(NativeProcess, "_max_log", 1024)
    code = ("import json,sys;print(json.dumps({'event':'ready'}),flush=True);"
            "r=json.loads(sys.stdin.readline());sys.stderr.write('x'*100000);sys.stderr.flush();"
            "print(json.dumps({'id':r['id'],'ok':True}),flush=True);sys.stdin.readline()")
    rpc = NativeProcess([sys.executable, "-u", "-c", code], ready_event="ready", log_path=tmp_path / "log")
    rpc.request("noise")
    rpc.close()
    assert (tmp_path / "log").stat().st_size == 1024
