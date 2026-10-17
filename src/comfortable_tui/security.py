"""Immutable browser capability policy. Host Python/native code remains trusted."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import re
from urllib.parse import urlsplit


SECURITY_PROTOCOL = 1
_TOPIC = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def web_origin(url: str, *, exact=False, allow_insecure_http=False) -> str:
    """Canonical network origin; opaque/file origins and credentials are never grants."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("URL must be bounded and contain no controls or whitespace")
    if "\\" in url or "%" in urlsplit(url).netloc:
        raise ValueError("Ambiguous URL authority")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("Only credential-free HTTP(S) network origins are supported")
    if exact and (parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Origin grants cannot contain paths, queries or fragments")
    # stdlib IDNA2003 and Chromium UTS46 disagree (for example on ß).
    # Require explicit ASCII/punycode instead of granting a different domain.
    if not parts.hostname.isascii():
        raise ValueError("Use an explicit ASCII/punycode origin hostname")
    host = parts.hostname.lower()
    if host.endswith(".") or "*" in host or not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise ValueError("Invalid or ambiguous origin hostname")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
        # Chromium accepts shortened, octal and hexadecimal IPv4 forms; never
        # turn an alternate spelling into a loopback grant.
        if re.fullmatch(r"[0-9.xa-f]+", host) and (host[0].isdigit() or host.startswith("0x")):
            raise ValueError("Use canonical IP addresses")
    if parts.scheme == "http" and not allow_insecure_http and not (host == "localhost" or ip and ip.is_loopback):
        raise PermissionError("Cleartext HTTP requires explicit policy; loopback is allowed")
    port = parts.port  # Reject malformed/out-of-range ports before native IPC.
    if port == 0:
        raise ValueError("Origin port must be positive")
    if ip:
        host = ip.compressed
    host = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port is not None and port != (443 if parts.scheme == "https" else 80) else ""
    return f"{parts.scheme}://{host}{suffix}"


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
    """No devices, files, downloads, popups, external protocols or eval by default.

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
            values = getattr(self, name)
            if isinstance(values, str) or len(values) > (8 if name == "navigation_origins" else 64):
                raise ValueError("Origins must be a bounded sequence")
            canonical = tuple(dict.fromkeys(web_origin(v, exact=True, allow_insecure_http=self.allow_insecure_http)
                                             for v in values))
            object.__setattr__(self, name, canonical)

    def bind(self, url, origins=()):
        if isinstance(origins, (str, bytes)):
            raise ValueError("Bridge origins must be a sequence")
        bridge = tuple(origins)
        if len(bridge) > 64:
            raise ValueError("Too many bridge origins")
        bridge = tuple(dict.fromkeys(web_origin(v, exact=True, allow_insecure_http=self.allow_insecure_http) for v in bridge))
        initial = () if url == "about:blank" else (web_origin(url, allow_insecure_http=self.allow_insecure_http),)
        navigation = self.navigation_origins or initial
        resources = self.resource_origins or navigation
        if initial and initial[0] not in navigation:
            raise PermissionError("Initial URL is outside navigation origins")
        if not set(bridge).issubset(navigation):
            raise PermissionError("Bridge origins must be a subset of navigation origins")
        wire = asdict(self)
        wire.update(protocol=SECURITY_PROTOCOL, navigation_origins=list(navigation), resource_origins=list(resources))
        return wire, bridge
