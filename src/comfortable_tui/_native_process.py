"""Bounded JSON control RPC. Image bytes never pass through this channel."""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import json
import os
from pathlib import Path
import subprocess
import threading


class NativeProcess:
    def __init__(self, command, *, ready_event, log_path, env=None, timeout=30):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        self._lock = threading.RLock()
        self._pending = {}
        self._events = deque(maxlen=1024)
        self._next = 1
        self._closing = False
        self._ready = Future()
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self._log, text=True, encoding="utf-8", bufsize=1,
                                            env=env, creationflags=0x08000000 if os.name == "nt" else 0)
            self._thread = threading.Thread(target=self._read, args=(ready_event,), daemon=True,
                                            name="ctui-native-control")
            self._thread.start()
            self.capabilities = self._ready.result(timeout=timeout)
        except BaseException as exc:
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait(timeout=5)
                self.process.stdin.close()
                if hasattr(self, "_thread"):
                    self._thread.join(timeout=3)
                self.process.stdout.close()
            self._log.close()
            if isinstance(exc, Exception):
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-4096:].strip()
                if tail:
                    raise RuntimeError(f"{exc}\nNative startup diagnostics:\n{tail}") from exc
            raise

    def _read(self, ready_event):
        failure = None
        try:
            for line in self.process.stdout:
                if len(line) > 1024 * 1024:
                    raise RuntimeError("Native control response exceeds 1 MiB")
                message = json.loads(line)
                with self._lock:
                    if "id" in message:
                        future = self._pending.pop(message["id"], None)
                        if future and not future.cancelled():
                            if message.get("ok"):
                                future.set_result(message.get("result", {}))
                            else:
                                future.set_exception(RuntimeError(message.get("error", "Native operation failed")))
                    elif message.get("event") == ready_event and not self._ready.done():
                        self._ready.set_result(message)
                    else:
                        self._events.append(message)
        except BaseException as exc:
            failure = exc
        finally:
            with self._lock:
                error = RuntimeError(f"Native helper connection closed; diagnostics: {self.log_path}"
                                     + (f" ({failure})" if failure else ""))
                if not self._ready.done():
                    self._ready.set_exception(error)
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(error)
                self._pending.clear()
                self._events.append({"event": "helper_exit", "error": str(error)})

    def send(self, op, **fields):
        with self._lock:
            if self._closing or self.process.poll() is not None:
                raise RuntimeError("Native helper is closed")
            if len(self._pending) >= 256:
                raise RuntimeError("Too many pending native control requests")
            request = self._next
            self._next += 1
            data = json.dumps(dict(fields, op=op, id=request), ensure_ascii=True, separators=(",", ":"))
            if len(data) > 1024 * 1024:
                raise ValueError("Native control message exceeds 1 MiB")
            future = Future()
            self._pending[request] = future
            future._native_request = request
            def forget_cancelled(done):
                if done.cancelled():
                    with self._lock:
                        self._pending.pop(request, None)
            future.add_done_callback(forget_cancelled)
            try:
                self.process.stdin.write(data + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._pending.pop(request, None)
                raise RuntimeError("Native helper input closed") from None
            return future

    def request(self, op, *, timeout=15, **fields):
        future = self.send(op, **fields)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            with self._lock:
                self._pending = {key: value for key, value in self._pending.items() if value is not future}
            raise TimeoutError("Native control request timed out") from None

    def events(self):
        with self._lock:
            result = list(self._events)
            self._events.clear()
            return result

    def close(self):
        if self._closing:
            return
        try:
            if self.process.poll() is None:
                try:
                    self.request("shutdown", timeout=5)
                except (RuntimeError, TimeoutError, OSError):
                    pass
        finally:
            self._closing = True
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self._thread.join(timeout=3)
            self.process.stdout.close()
            self._log.close()
