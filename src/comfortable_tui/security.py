"""Immutable browser capability policy. Host Python/native code remains trusted."""
from __future__ import annotations

from dataclasses import dataclass
import re


SECURITY_PROTOCOL = 2
_TOPIC = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def url_text(url):
    """Shape check only. CEF is the single authority for URL/origin semantics."""
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        raise ValueError("URL must be a nonempty string of at most 8192 characters")
    return url


def origin_names(values, maximum=64):
    if isinstance(values, (str, bytes)):
        raise ValueError("Origins must be a bounded sequence")
    values = tuple(values)
    if len(values) > maximum:
        raise ValueError("Too many origins")
    return tuple(dict.fromkeys(url_text(value) for value in values))


def topic_names(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Topics must be a sequence of identifiers")
    values = tuple(values)
    if len(values) > 64 or any(not isinstance(v, str) or not _TOPIC.fullmatch(v) for v in values):
        raise ValueError("At most 64 bounded, exact topic identifiers are allowed")
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class BrowserLimits:
    max_pages: int = 16
    max_frame_bytes: int = 64 * 1024 * 1024
    max_total_frame_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        for value, maximum in ((self.max_pages, 64), (self.max_frame_bytes, 512 * 1024 * 1024),
                               (self.max_total_frame_bytes, 1024 * 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Browser limits must be positive bounded integers")
        if self.max_frame_bytes > self.max_total_frame_bytes:
            raise ValueError("Per-frame budget exceeds total budget")

    def frame_bytes(self, width, height):
        if any(type(n) is not int or not 1 <= n <= 4096 for n in (width, height)):
            raise ValueError("Browser frame dimensions must be integers in 1..4096")
        size = 64 + width * height * 4 * 3
        if size > self.max_frame_bytes:
            raise PermissionError("Browser frame exceeds the per-page buffer budget")
        return size


@dataclass(frozen=True)
class BrowserPolicy:
    """Per-page settings; a service policy supplies defaults, not an ACL parent.

    Origin lists are exact allowlists, never glob patterns. An empty list binds
    to the initial network origin. Resource filtering is not an OS firewall.
    """
    navigation_origins: tuple[str, ...] = ()
    resource_origins: tuple[str, ...] = ()
    allow_insecure_http: bool = False
    allow_evaluate: bool = False

    def __post_init__(self):
        if type(self.allow_insecure_http) is not bool or type(self.allow_evaluate) is not bool:
            raise ValueError("Policy switches must be booleans")
        for name in ("navigation_origins", "resource_origins"):
            object.__setattr__(self, name, origin_names(getattr(self, name), 8 if name == "navigation_origins" else 64))
