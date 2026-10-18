"""Adversarial real-CEF boundary checks against two disposable loopback origins."""
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time
import uuid

from comfortable_tui import BrowserPolicy, BrowserService, FrameBuffer


def wait(service, predicate, timeout=10):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        service.poll()
        if predicate():
            return
        time.sleep(.01)
    raise TimeoutError("Security fixture did not finish")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--platform", choices=("x11", "wayland"))
    parser.add_argument("--render-mode", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    class RenderService(BrowserService):
        def open(self, *values, **options):
            options.setdefault("render_mode", args.render_mode)
            return super().open(*values, **options)

    def frame_sequence(page):
        return page.frame_stats()["published_frames"] if args.render_mode == "gpu" else page.source.sequence
    if args.report.exists():
        parser.error("choose a new report path")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.server.server_port, self.path))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", origin_b + "/probe")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if self.path == "/download":
                self.send_header("Content-Disposition", "attachment; filename=ctui-security-fixture.txt")
            if self.path == "/strict":
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; object-src 'none'")
            self.end_headers()
            self.wfile.write(b"probe" if self.path == "/probe" else b"<!doctype html><title>Security fixture</title><p>Test</p>")

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    origin_a, origin_b = [f"http://127.0.0.1:{s.server_port}" for s in servers]
    result = {"checks": [], "result": "failed", "render_mode": args.render_mode}
    try:
        # Evaluation is an explicit host-only diagnostic grant, not a webpage API.
        with RenderService(executable=args.browser, display_platform=args.platform,
                            policy=BrowserPolicy(allow_evaluate=True, resource_origins=(origin_a, origin_b))) as service:
            page = service.open(origin_a, origins=[origin_a], publish_topics=["probe"], subscribe_topics=["probe"],
                                policy=BrowserPolicy(allow_evaluate=True, resource_origins=(origin_a,)))
            wait(service, lambda: page.loaded)
            initial = service._rpc.request("security_state", view=page.id)
            started = time.perf_counter()
            same = service.open("about:blank", policy=BrowserPolicy(navigation_origins=(origin_a,)))
            cached_ms = (time.perf_counter() - started) * 1000
            cached = service._rpc.request("security_state", view=same.id)
            assert initial["profile_initializations"] == cached["profile_initializations"] == 1
            assert initial["profile_permission_pairs"] == cached["profile_permission_pairs"] == 1
            same.close()
            combined = BrowserPolicy(navigation_origins=(origin_a, origin_b))
            for _ in range(2):
                extra = service.open("about:blank", policy=combined)
                expanded = service._rpc.request("security_state", view=extra.id)
                assert expanded["profile_initializations"] == 1 and expanded["profile_permission_pairs"] == 4
                extra.close()
            result["profile_cache"] = dict(initial_pairs=1, repeated_pairs=1, expanded_pairs=4,
                                           repeated_expanded_pairs=4, cached_open_ms=round(cached_ms, 3))
            result["checks"].append("profile_initializes_once_and_only_configures_new_origin_pairs")

            for invalid in ("file:///ctui-forbidden", "data:text/html,hi", "javascript:42", "null",
                            "https://*.example.com", "https://user:pass@example.com", "https://example.com/path",
                            "https://example.com\\@evil.test", "https://example.com\n", "http://example.com",
                            "https://example.com:0", "https://example.com:65536"):
                try:
                    service.open("about:blank", policy=BrowserPolicy(navigation_origins=(invalid,)), width=8, height=8)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("native origin grant accepted: " + repr(invalid))
            for spelling, expected in (("https://EXAMPLE.com:443/", "https://example.com"),
                                       ("https://faß.de", "https://xn--fa-hia.de"),
                                       ("http://127.1", "http://127.0.0.1"),
                                       ("http://[::1]:80/", "http://[::1]")):
                normalized = service.open("about:blank", policy=BrowserPolicy(navigation_origins=(spelling,)), width=8, height=8)
                assert normalized._security["navigation_origins"] == [expected]
                normalized.close()
            try:
                service.open("about:blank", origins=[origin_b], policy=BrowserPolicy(navigation_origins=(origin_a,)), width=8, height=8)
            except RuntimeError:
                pass
            else:
                raise AssertionError("bridge outside native navigation policy")
            result["checks"].append("cef_is_the_single_origin_parser_including_unicode_and_ipv4")

            assert not initial["audio_muted"]
            page.set_muted(True)
            assert service._rpc.request("security_state", view=page.id)["audio_muted"]
            page.set_muted(False)
            assert not service._rpc.request("security_state", view=page.id)["audio_muted"]
            result["checks"].append("audio_is_explicit_host_control_instead_of_a_fixed_permission")
            no_eval = service.open(origin_a, policy=BrowserPolicy())
            wait(service, lambda: no_eval.loaded)
            try:
                service._rpc.request("eval", view=no_eval.id, code="42")
            except RuntimeError:
                result["checks"].append("native_eval_denied_even_when_python_is_bypassed")
            else:
                raise AssertionError("native evaluation grant bypass")

            for url in (origin_b, "file:///ctui-security-forbidden", "javascript:42", "data:text/html,secret"):
                try:
                    service._rpc.request("navigate", view=page.id, url=url)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("native navigation bypass: " + url)
            result["checks"].append("raw_rpc_navigation_and_local_schemes_denied")

            page.evaluate("location.href=" + json.dumps(origin_b) + ";true")
            time.sleep(.1)
            assert page.evaluate("location.origin") == origin_a
            assert service._rpc.request("security_state", view=page.id)["denied_navigations"] >= 1
            result["checks"].append("script_navigation_denied_in_native_handler")

            before = sum(port == servers[1].server_port for port, _ in requests)
            assert not page.evaluate("fetch(" + json.dumps(origin_b + "/probe") + ").then(()=>true,()=>false)")
            assert not page.evaluate("fetch('/redirect').then(r=>r.text()).then(t=>t==='probe',()=>false)")
            after = sum(port == servers[1].server_port for port, _ in requests)
            assert before == after
            result["checks"].append("cross_origin_and_redirect_requests_never_reach_fixture_server")

            allowed = service.open(origin_a, origins=[origin_a], policy=BrowserPolicy(
                allow_evaluate=True, resource_origins=(origin_a, origin_b)))
            wait(service, lambda: allowed.loaded)
            assert allowed.evaluate("fetch(" + json.dumps(origin_b + "/probe") + ").then(r=>r.text())") == "probe"
            result["checks"].append("explicit_resource_grant_works_without_navigation_grant")

            for op in ("shell", "open", "read_file", "eval"):
                code = "new Promise(r=>cefQuery({request:JSON.stringify({op:" + json.dumps(op) + "}),onSuccess:()=>r(false),onFailure:()=>r(true)}))"
                assert page.evaluate(code)
            assert page.evaluate("(()=>{let p={};for(let i=0;i<40;i++)p={nested:p};return comfortable.publish('probe',p).then(()=>false,()=>true)})()")
            pending = page.evaluate_async("Promise.all(Array.from({length:800},()=>comfortable.publish('probe',{}).then(()=>0,()=>1))).then(a=>{document.title='rate-denied:'+a.reduce((s,v)=>s+v,0);return true})")
            wait(service, lambda: page.title.startswith("rate-denied:"))
            assert int(page.title.split(":")[1]) > 0
            pending.cancel()
            service._rpc.request("cancel_eval", view=page.id, request=pending._native_request)
            time.sleep(2.1)  # Diagnostic replies share the same bounded query bucket.
            result["checks"].append("bridge_unknown_ops_nested_payload_and_flood_denied")

            # Permission callbacks are native; no native OS chooser or browser prompt opens.
            assert page.evaluate("new Promise(r=>navigator.geolocation.getCurrentPosition(()=>r(0),e=>r(e.code),{timeout:2000}))") == 1
            assert page.evaluate("Notification.requestPermission()") == "denied"
            permissions = page.evaluate("Promise.all(['camera','microphone','geolocation','notifications','clipboard-read'].map(name=>navigator.permissions.query({name}).then(p=>p.state)))")
            assert permissions == ["denied"] * 5
            assert page.evaluate("alert('blocked');true")
            state = service._rpc.request("security_state", view=page.id)
            result["native_state"] = state
            assert state["content_permissions_blocked"] and state["denied_dialogs"] >= 1
            assert state["printing_disabled"] and state["password_manager_disabled"]
            assert page.evaluate("window.print();true")
            result["checks"].append("native_content_permissions_printing_password_and_dialogs_deny")

            def click(code):
                previous = frame_sequence(page)
                page.evaluate("document.body.innerHTML='<button style=\"position:absolute;left:0;top:0;width:100px;height:50px\">Test</button><input type=file hidden>';"
                              "document.querySelector('button').onclick=()=>{" + code + "};true")
                wait(service, lambda: frame_sequence(page) > previous)
                page.input("focus_in")
                page.input("mouse_down", x=20, y=20, buttons=1, button="left")
                page.input("mouse_up", x=20, y=20, buttons=0, button="left")

            click("document.querySelector('input').click()")
            wait(service, lambda: service._rpc.request("security_state", view=page.id)["denied_dialogs"] > state["denied_dialogs"])
            assert page.evaluate("document.querySelector('input').value") == ""
            click("const a=document.createElement('a');a.href='/download';a.download='ctui-security-fixture.txt';a.click()")
            wait(service, lambda: service._rpc.request("security_state", view=page.id)["denied_downloads"] > 0)
            result["checks"].append("native_file_chooser_and_download_callbacks_deny_after_user_click")
            state = service._rpc.request("security_state", view=page.id)
            result["native_state"] = state

            strict = service.open(origin_a + "/strict", origins=[origin_a])
            wait(service, lambda: strict.loaded)
            try:
                strict.evaluate("42")
            except RuntimeError as error:
                assert "Content Security Policy" in str(error)
            else:
                raise AssertionError("CSP bypass")
            result["checks"].append("host_eval_opt_in_does_not_bypass_csp")

            with FrameBuffer(8, 8, pixel_format="bgra8_premultiplied") as buffer:
                for bad_origins in (["file://"], [origin_a + "/path"], ["https://*.example.com"]):
                    try:
                        service._rpc.request("open", view=uuid.uuid4().hex, profile="security_fixture",
                            scope="fixture", url=origin_a, source=asdict(buffer.info), fps=60,
                            security=dict(page._security, navigation_origins=bad_origins), limits=asdict(service.limits))
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("native malformed policy accepted")
            result["checks"].append("native_open_revalidates_malformed_policy")
            with FrameBuffer(8, 8, pixel_format="bgra8_premultiplied") as buffer:
                for field in ("width", "stride", "header"):
                    source = asdict(buffer.info)
                    if field == "width":
                        source["width"] = 5000
                    elif field == "stride":
                        source["stride"] = 0
                    else:
                        struct.pack_into("<i", buffer._shm.buf, 8, 99)
                    try:
                        service._rpc.request("open", view=uuid.uuid4().hex, profile="security_fixture",
                            scope="fixture", url=origin_a, source=source, fps=60,
                            security=page._security, limits=asdict(service.limits))
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Native malformed frame accepted")
                result["checks"].append("native_frame_dimensions_stride_and_corrupt_header_denied")
            with tempfile.TemporaryDirectory(prefix="ctui-denied-switch-") as folder:
                for switch in ("--no-sandbox=1", "--ignore-certificate-errors=true", "--remote-debugging-port=9222"):
                    command = [str(service.executable), "--ctui-root=" + folder, "--no-first-run", switch]
                    rejected = subprocess.run(command, input="", capture_output=True, text=True,
                        timeout=15, creationflags=0x08000000 if sys.platform == "win32" else 0)
                    assert rejected.returncode != 0 and "unsafe browser launch switch" in rejected.stderr
            result["checks"].append("native_startup_rejects_unsafe_switch_value_forms")
            with tempfile.TemporaryDirectory(prefix="ctui-saved-permissions-") as folder:
                options = dict(application_id="verify.security.saved", data_root=folder, executable=service.executable,
                               display_platform=args.platform, policy=BrowserPolicy(allow_evaluate=True))
                with RenderService(**options) as seed:
                    saved = seed.open(origin_a, profile=seed.profile("account", persistent=True), origins=[origin_a])
                    wait(seed, lambda: saved.loaded)
                    preferences = Path(saved.info["cache_path"]) / "Preferences"
                content = json.loads(preferences.read_text(encoding="utf-8"))
                exceptions = content["profile"]["content_settings"]["exceptions"]
                for category in ("notifications", "geolocation"):
                    assert exceptions[category]
                    for entry in exceptions[category].values():
                        entry["setting"] = 1  # Simulate a prior runtime's saved ALLOW; no OS grant is requested.
                preferences.write_text(json.dumps(content), encoding="utf-8")
                with RenderService(**options) as restored:
                    saved = restored.open(origin_a, profile=restored.profile("account", persistent=True), origins=[origin_a])
                    wait(restored, lambda: saved.loaded)
                    assert saved.evaluate("Notification.permission") == "denied"
                    assert saved.evaluate("navigator.permissions.query({name:'geolocation'}).then(p=>p.state)") == "denied"
                result["checks"].append("saved_allow_permissions_replaced_before_document_load")
            result["native_state"] = state
            result["capabilities"] = service.capabilities
            page.navigate("about:blank")
            wait(service, lambda: page.loaded and page.url == "about:blank")
            assert service._rpc.request("security_state", view=page.id)["subscriptions"] == 0
            result["checks"].append("explicit_blank_navigation_completes_and_drops_subscriptions")
            result["result"] = "passed"
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
