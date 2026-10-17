"""Native GPU frame descriptors. Python never acquires a per-frame texture/pixel lease."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import os


def gpu_available():
    if os.name != "nt":
        return False
    try:
        module = importlib.import_module("comfortable_tui._native_gpu")
        return module.protocol_version == 1
    except (ImportError, OSError):
        return False


@dataclass(frozen=True)
class GpuFrameInfo:
    name: str
    width: int
    height: int
    adapter_low: int
    adapter_high: int
    version: int = 1
    pixel_format: str = "bgra8_premultiplied"

    def __post_init__(self):
        prefix = "Local\\CTUI_GPU_"
        if (not self.name.startswith(prefix) or len(self.name) != len(prefix) + 32 or
                any(c not in "0123456789abcdef" for c in self.name[len(prefix):])):
            raise ValueError("invalid native GPU channel name")
        if (self.version != 1 or not isinstance(self.width, int) or not isinstance(self.height, int) or
                not 1 <= self.width <= 16384 or not 1 <= self.height <= 16384):
            raise ValueError("unsupported GPU frame protocol or size")
        if not 0 <= self.adapter_low < 2**32 or not -2**31 <= self.adapter_high < 2**31:
            raise ValueError("invalid GPU adapter LUID")
        if self.pixel_format != "bgra8_premultiplied":
            raise ValueError("native GPU sources require premultiplied BGRA8")


class GpuFrameSource:
    """A handshake descriptor, not a pixel buffer. The C++ provider owns resources.

    source = GpuFrameSource.attach(channel_name)
    canvas = OverlayCanvas(source)
    Keep the provider alive until the canvas's detached event or App.close().
    """

    def __init__(self, info: GpuFrameInfo):
        if not isinstance(info, GpuFrameInfo):
            raise TypeError("info must be GpuFrameInfo")
        self.info, self._closed = info, False

    @classmethod
    def attach(cls, name):
        if not gpu_available():
            raise RuntimeError("GPU frames require the Windows COMFORTABLE_TUI_BUILD_GPU=1 extension; see docs/native-gpu.md")
        from . import _native_gpu
        return cls(GpuFrameInfo(**_native_gpu.describe(str(name))))

    def _check_open(self):
        if self._closed:
            raise RuntimeError("GPU source descriptor is closed")

    def close(self):
        self._closed = True  # Does not terminate the native provider or other views.

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *args):
        self.close()


class _FrameReference:
    """GUI control-plane dimensions only; C++ owns the actual buffer attachments."""
    def __init__(self, info):
        self.info = info

    def close(self):
        pass
