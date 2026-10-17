"""App-isolated local browser analytics, separate from the frame protocol."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlencode

from comfortable_tui import BrowserService, WebView


class WebPanels:
    group = "comfortable-top-analytics"

    def __init__(self, platform=None):
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
        handler = partial(Handler, directory=str(Path(__file__).parent / "web"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.service = None
        self.pages = {}
        self.on_select = None
        self.probes = {}
        self.loaded = set()
        try:
            self.service = BrowserService("org.comfortabletui.examples.comfortabletop", display_platform=platform)
            self.profile = self.service.profile("local-analytics", persistent=False)
            self.service.on_event(self._event)
        except BaseException:
            self.close()
            raise

    def widget(self, key="overview"):
        if key not in self.pages:
            query = {"mode": "overview"} if key == "overview" else {
                "mode": "process", "pid": key[0], "created": key[1]}
            origin = f"http://127.0.0.1:{self.server.server_port}"
            self.pages[key] = self.service.open(origin + "/analytics.html?" + urlencode(query),
                profile=self.profile, width=720, height=400, fps=60, origins=[origin],
                subscribe_topics=["monitor.sample", "monitor.probe"],
                publish_topics=["monitor.select", "monitor.proof"], message_group=self.group)
        return WebView(self.pages[key], fit="contain")

    def _event(self, event):
        if event["event"] == "loaded":
            self.loaded.add(event["view"])
        if event["event"] == "message" and event.get("data", {}).get("topic") == "monitor.select":
            data = event["data"].get("payload", {})
            if isinstance(data, dict) and type(data.get("pid")) is int and isinstance(data.get("created"), (int, float)):
                if self.on_select:
                    self.on_select((data["pid"], data["created"]))
        if event["event"] == "message" and event.get("data", {}).get("topic") == "monitor.proof":
            data = event["data"].get("payload", {})
            if isinstance(data, dict) and isinstance(data.get("token"), str):
                self.probes[event["view"]] = data

    def probe(self, token, key):
        self.probes.clear()
        self.service.publish("monitor.probe", {"token": token, "pid": key[0], "created": key[1]},
                             profile=self.profile, message_group=self.group)

    def publish(self, model):
        payload = model.payload()
        payload["watched"] = [{"pid": k[0], "created": k[1], "history": list(model.history.get(k, ())),
                               "process": vars(model.process(k)) if model.process(k) else None} for k in model.pins]
        self.service.publish("monitor.sample", payload, profile=self.profile, message_group=self.group)

    def close(self):
        if self.service:
            self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
