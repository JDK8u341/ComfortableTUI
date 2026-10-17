"""Real CEF storage, JS messaging, input and native frame verification.

Runs against a local fixture server only. No external accounts or browser profiles.
Linux: run separately with --platform x11 and --platform wayland.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time

from comfortable_tui.browser import BrowserService
from comfortable_tui import BrowserPolicy


def wait(service, condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        service.poll()
        if condition():
            return
        time.sleep(.02)
    raise TimeoutError("Browser verification condition timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--platform", choices=("x11", "wayland"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--data-root", type=Path, help="Keep test profiles and logs for diagnosis")
    args = parser.parse_args()
    directory = Path(__file__).parent / "web"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, directory=str(directory), **kwargs)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    options = dict(executable=args.browser, display_platform=args.platform,
                   policy=BrowserPolicy(allow_evaluate=True))
    result = {"platform": args.platform or "windows", "checks": [], "capabilities": {}}
    try:
        with (nullcontext(args.data_root) if args.data_root else
              tempfile.TemporaryDirectory(prefix="ctui-verify-profiles-")) as data:
            with BrowserService("verify.app.a", data_root=data, **options) as a:
                result["capabilities"] = a.capabilities
                assert a.capabilities["startup_ui_suppressed"]
                account = a.profile("account-a", persistent=True)
                other = a.profile("account-b")
                fixture = dict(origins=[origin], publish_topics=["selection.changed"],
                               subscribe_topics=["selection.changed"], message_group="explicit-shared-group")
                first = a.open(origin + "/demo.html", profile=account, **fixture)
                second = a.open(origin + "/demo.html", profile=other, **fixture)
                wait(a, lambda: first.loaded and second.loaded and first.source.sequence > 0)
                assert first.evaluate("typeof comfortable.publish") == "function"
                assert first.evaluate("document.cookie='token=app-a;path=/;max-age=3600'; localStorage.setItem('owner','app-a'); true")
                assert "app-a" in first.evaluate("document.cookie")
                assert "app-a" not in second.evaluate("document.cookie")
                assert second.evaluate("localStorage.getItem('owner')") is None
                result["checks"].append("independent_profiles")
                third = a.open(origin + "/demo.html", profile=account, **fixture)
                wait(a, lambda: third.loaded)
                assert "app-a" in third.evaluate("document.cookie")
                assert third.evaluate("localStorage.getItem('owner')") == "app-a"
                result["checks"].append("explicit_shared_profile")
                wait(a, lambda: first.evaluate("document.readyState") == "complete")
                assert first.evaluate("comfortable.publish('selection.changed',{item_id:73})") is not None
                wait(a, lambda: second.evaluate("received.some(x=>x.item_id===73)"))
                result["checks"].append("authorized_cross_profile_message")
                assert first.evaluate("comfortable.publish('forbidden',{}).then(()=>false,()=>true)")
                result["checks"].append("topic_denied")
                assert first.evaluate("comfortable.publish('selection.changed',{text:'x'.repeat(70000)}).then(()=>false,()=>true)")
                result["checks"].append("oversized_message_denied")
                assert first.evaluate("new Promise(resolve=>{const f=document.createElement('iframe'); "
                    "f.srcdoc='<p>untrusted child</p>'; f.onload=()=>f.contentWindow.cefQuery({"
                    "request:JSON.stringify({op:\"publish\",topic:\"selection.changed\",payload:{item_id:999}}),"
                    "onSuccess:()=>resolve(false),onFailure:()=>resolve(true)});document.body.append(f)})")
                result["checks"].append("child_frame_bridge_denied")
                assert first.evaluate("new Promise(resolve=>cefQuery({request:JSON.stringify({"
                    "op:'eval_result',request:2147483646,value:'forged'}),onSuccess:()=>resolve(false),"
                    "onFailure:()=>resolve(true)}))")
                result["checks"].append("unrequested_evaluation_result_denied")
                try:
                    first.evaluate("new Promise(()=>{})", timeout=.05)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError("Unresolved evaluation should time out")
                assert first.evaluate("42") == 42
                result["checks"].append("evaluation_timeout_and_recovery")
                with BrowserService("verify.app.b", data_root=data, **options) as b:
                    assert b.capabilities["startup_ui_suppressed"]
                    isolated = b.open(origin + "/demo.html", **fixture)
                    wait(b, lambda: isolated.loaded)
                    assert "app-a" not in isolated.evaluate("document.cookie")
                    assert isolated.evaluate("localStorage.getItem('owner')") is None
                    assert isolated.evaluate("received.length") == 0
                    first.evaluate("comfortable.publish('selection.changed',{item_id:99})")
                    time.sleep(.05)
                    assert isolated.evaluate("received.length") == 0
                    result["checks"].append("application_storage_and_message_isolation")
                before = first.source.sequence
                first.input("focus_in").result(timeout=5)
                first.evaluate("document.querySelector('#editor').focus()")
                first.input("text", commit="中文验证").result(timeout=5)
                wait(a, lambda: first.evaluate("document.querySelector('#editor').value") == "中文验证")
                result["checks"].append("unicode_commit")
                time.sleep(.15)
                assert first.source.sequence > before
                with first.source.read() as frame:
                    assert frame and len(set(frame.pixels[:10000])) > 2
                result["checks"].append("native_frame_pixels")
                second.reload()
                wait(a, lambda: second.loaded)
                assert second.evaluate("received.length") == 0
                result["checks"].append("navigation_subscription_reset")
            with BrowserService("verify.app.a", data_root=data, **options) as restored:
                assert restored.capabilities["startup_ui_suppressed"]
                page = restored.open(origin + "/demo.html", profile=restored.profile("account-a", persistent=True),
                                     origins=[origin])
                wait(restored, lambda: page.loaded)
                assert "app-a" in page.evaluate("document.cookie")
                assert page.evaluate("localStorage.getItem('owner')") == "app-a"
                result["checks"].append("persistent_restart")
            # Each anonymous service gets a different, empty root. All must start
            # unattended, including without an existing First Run sentinel.
            for _ in range(2):
                with BrowserService(timeout=15, **options) as fresh:
                    assert fresh.capabilities["startup_ui_suppressed"]
                    page = fresh.open(origin + "/demo.html")
                    wait(fresh, lambda: page.loaded and page.source.sequence > 0)
            result["checks"].append("unattended_fresh_root_startup")
    finally:
        server.shutdown()
        server.server_close()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
