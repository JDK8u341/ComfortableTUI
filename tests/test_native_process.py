"""Exercise the native control lifecycle without browser/GUI dependencies."""
import sys
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest

from comfortable_tui._native_process import NativeProcess


CHILD = r'''
import json, sys
print(json.dumps({"event":"ready","protocol":1}),flush=True)
for line in sys.stdin:
    r=json.loads(line)
    if r['op']=='hang':
        continue
    if r['op']=='crash':
        sys.exit(7)
    ok=r['op']!='fail'
    print(json.dumps({'id':r['id'],'ok':ok,'error':'deliberate failure','result':{'echo':r['op']}}),flush=True)
    if r['op']=='shutdown':
        break
'''


def test_timeout_cancels_pending_and_connection_recovers(tmp_path):
    rpc = NativeProcess([sys.executable, "-u", "-c", CHILD], ready_event="ready", log_path=tmp_path / "helper.log")
    try:
        with pytest.raises(TimeoutError):
            rpc.request("hang", timeout=.01)
        assert not rpc._pending
        assert rpc.request("hello") == {"echo": "hello"}
        with pytest.raises(RuntimeError, match="deliberate"):
            rpc.request("fail")
    finally:
        rpc.close()
    assert rpc.process.poll() == 0
    rpc.close()


def test_child_exit_fails_all_waiters(tmp_path):
    rpc = NativeProcess([sys.executable, "-u", "-c", CHILD], ready_event="ready", log_path=tmp_path / "helper.log")
    try:
        waiting = rpc.send("hang")
        crashed = rpc.send("crash")
        for future in (waiting, crashed):
            with pytest.raises(RuntimeError, match="connection closed"):
                future.result(timeout=3)
        assert not rpc._pending
    finally:
        rpc.close()


def test_startup_timeout_reaps_its_child(tmp_path):
    with pytest.raises(FutureTimeoutError):
        NativeProcess([sys.executable, "-u", "-c", "import sys; sys.stdin.read()"],
                      ready_event="ready", log_path=tmp_path / "startup.log", timeout=.03)
