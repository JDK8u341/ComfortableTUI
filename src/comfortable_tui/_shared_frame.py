"""Named, bounded frame transport. No Qt dependency and no pixel IPC messages."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from multiprocessing import shared_memory
import os
import struct
import tempfile
import threading
import time
import uuid


class _NamedLock:
    """Attachable interprocess lock; descriptors work outside multiprocessing.spawn."""

    def __init__(self, name):
        self._local = threading.Lock()
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self._api = ctypes.WinDLL("kernel32", use_last_error=True)
            self._api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self._api.CreateMutexW.restype = wintypes.HANDLE
            self._api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self._api.WaitForSingleObject.restype = wintypes.DWORD
            self._api.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self._api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self._api.CreateMutexW(None, False, "Local\\" + name)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self.path = os.path.join(tempfile.gettempdir(), name + ".lock")
            self.handle = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)

    def __enter__(self):
        self._local.acquire()
        try:
            if os.name == "nt":
                # WAIT_ABANDONED also grants ownership after a dead process.
                result = self._api.WaitForSingleObject(self.handle, 0xffffffff)
                if result not in (0, 0x80):
                    raise OSError("could not acquire frame mutex")
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX)
        except BaseException:
            self._local.release()
            raise
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            self._api.ReleaseMutex(self.handle)
        else:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        self._local.release()

    def close(self, *, unlink=False):
        if os.name == "nt":
            self._api.CloseHandle(self.handle)
        else:
            os.close(self.handle)
            if unlink:
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass


_FORMATS = {"rgba8": 4, "bgra8": 4, "bgra8_premultiplied": 4, "rgb8": 3}
_HEADER = struct.Struct("<8siiQd")
_MAGIC = b"CTUIFR01"
_OFFSET = 64


@dataclass(frozen=True)
class FrameBufferInfo:
    """Picklable descriptor; attach in another local process with FrameBuffer.attach."""

    name: str
    width: int
    height: int
    stride: int
    pixel_format: str

    @property
    def frame_bytes(self):
        return self.stride * self.height


@dataclass(frozen=True)
class FrameLease:
    pixels: memoryview
    sequence: int
    timestamp: float


class FrameBuffer:
    """Three slots, one producer and one consumer, newest completed frame wins.

    write() lends writable pixels; leaving the context publishes atomically.
    read() pins a published slot until its context exits. Views must not escape
    either lease. close the consumer before closing the creating buffer.
    """

    def __init__(self, width: int, height: int, *, pixel_format="rgba8", stride=None,
                 max_bytes=512 * 1024 * 1024):
        if pixel_format not in _FORMATS:
            raise ValueError(f"pixel_format must be one of {tuple(_FORMATS)}")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise ValueError("frame dimensions must be positive integers")
        row = width * _FORMATS[pixel_format]
        stride = ((row + 3) // 4) * 4 if stride is None else stride
        if not isinstance(stride, int) or stride < row or stride % 4:
            raise ValueError("stride must cover a row and be a multiple of four bytes")
        size = _OFFSET + stride * height * 3
        if size > max_bytes:
            raise ValueError("frame allocation exceeds max_bytes (includes all three slots)")
        shm = shared_memory.SharedMemory(name="ctui_" + uuid.uuid4().hex, create=True, size=size)
        info = FrameBufferInfo(shm.name, width, height, stride, pixel_format)
        self._setup(info, shm, True)
        _HEADER.pack_into(shm.buf, 0, _MAGIC, -1, -1, 0, 0.0)

    def _setup(self, info, shm, owner):
        self.info, self._shm, self._owner = info, shm, owner
        self._closed = False
        self._lifetime = threading.Lock()
        self._leases = 0
        self._meta = _NamedLock(info.name + "_meta")
        self._writer = _NamedLock(info.name + "_writer")
        self._reader = _NamedLock(info.name + "_reader")

    @classmethod
    def attach(cls, info: FrameBufferInfo):
        if info.pixel_format not in _FORMATS or min(info.width, info.height, info.stride) <= 0:
            raise ValueError("invalid frame descriptor")
        if info.stride < info.width * _FORMATS[info.pixel_format] or info.stride % 4:
            raise ValueError("invalid frame descriptor stride")
        shm = shared_memory.SharedMemory(name=info.name)
        if shm.size < _OFFSET + info.frame_bytes * 3 or bytes(shm.buf[:8]) != _MAGIC:
            shm.close()
            raise ValueError("frame descriptor does not match shared memory")
        instance = cls.__new__(cls)
        instance._setup(info, shm, False)
        return instance

    @property
    def sequence(self):
        with self._lease(), self._meta:
            return _HEADER.unpack_from(self._shm.buf)[3]

    def _check_open(self):
        if self._closed:
            raise RuntimeError("frame buffer is closed")

    @contextmanager
    def _lease(self):
        with self._lifetime:
            self._check_open()
            self._leases += 1
        try:
            yield
        finally:
            with self._lifetime:
                self._leases -= 1

    @contextmanager
    def write(self, *, timestamp=None):
        self._check_open()
        timestamp = time.perf_counter() if timestamp is None else float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite")
        with self._lease(), self._writer:
            with self._meta:
                _, published, reading, _, _ = _HEADER.unpack_from(self._shm.buf)
                slot = next(i for i in range(3) if i not in (published, reading))
            start = _OFFSET + slot * self.info.frame_bytes
            pixels = self._shm.buf[start:start + self.info.frame_bytes]
            try:
                yield pixels
            except BaseException:
                raise  # A failed producer never publishes a partial frame.
            else:
                with self._meta:
                    _, _, reading, seq, _ = _HEADER.unpack_from(self._shm.buf)
                    _HEADER.pack_into(self._shm.buf, 0, _MAGIC, slot, reading, seq + 1, timestamp)
            finally:
                pixels.release()

    def submit(self, pixels, *, timestamp=None):
        """Copy a contiguous buffer-protocol object; source may be reused on return."""
        source = memoryview(pixels)
        try:
            if not source.c_contiguous or source.nbytes != self.info.frame_bytes:
                raise ValueError("pixels must be contiguous and include exactly stride * height bytes")
            raw = source.cast("B")
            try:
                with self.write(timestamp=timestamp) as destination:
                    destination[:] = raw
            finally:
                raw.release()
        finally:
            source.release()

    @contextmanager
    def read(self):
        self._check_open()
        with self._lease(), self._reader:
            with self._meta:
                _, slot, _, seq, timestamp = _HEADER.unpack_from(self._shm.buf)
                _HEADER.pack_into(self._shm.buf, 0, _MAGIC, slot, slot, seq, timestamp)
            pixels = None
            try:
                if slot < 0:
                    yield None
                else:
                    start = _OFFSET + slot * self.info.frame_bytes
                    pixels = self._shm.buf[start:start + self.info.frame_bytes].toreadonly()
                    yield FrameLease(pixels, seq, timestamp)
            finally:
                if pixels is not None:
                    pixels.release()
                with self._meta:
                    _, published, _, seq, timestamp = _HEADER.unpack_from(self._shm.buf)
                    _HEADER.pack_into(self._shm.buf, 0, _MAGIC, published, -1, seq, timestamp)

    def close(self):
        with self._lifetime:
            if self._closed:
                return
            if self._leases:
                raise BufferError("cannot close a frame buffer during a live lease")
            self._shm.close()
            if self._owner:
                self._shm.unlink()
            for lock in (self._meta, self._writer, self._reader):
                lock.close(unlink=self._owner)
            self._closed = True

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *_):
        self.close()
