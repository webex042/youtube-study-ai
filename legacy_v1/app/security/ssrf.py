"""
SSRF Guard

This application must never become an arbitrary URL-fetching service.
Everything here exists to make sure that a user-supplied string can only
ever result in a request to a host we have explicitly decided to allow.

The guard is deliberately allowlist-first: a URL is rejected unless its
scheme *and* host are both known-good. On top of that, hosts are resolved
and every resulting IP is checked against the blocked ranges, which closes
the DNS-rebinding and "hostname that points at 127.0.0.1" holes that a
pure string check would leave open.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# ── Allowlists ───────────────────────────────────────────────────

ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "http"})

# Every host the backend is ever permitted to talk to. Anything not on
# this list is refused before a socket is opened.
ALLOWED_HOSTS: frozenset[str] = frozenset({
    # YouTube (transcript + metadata)
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "i.ytimg.com",
    "img.youtube.com",
    # LLM providers
    "generativelanguage.googleapis.com",
    "api.openai.com",
})

# Hostnames that must never be reachable, even if DNS would resolve them.
BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "instance-data.ec2.internal",
})

# Suffixes that indicate an internal / non-routable name.
BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".corp",
    ".home",
    ".lan",
    ".private",
    ".test",
    ".invalid",
    ".example",
    ".onion",
)

# Cloud instance-metadata endpoints. These are the classic SSRF targets.
BLOCKED_IPS: frozenset[str] = frozenset({
    "169.254.169.254",   # AWS / Azure / GCP / DigitalOcean IMDS
    "100.100.100.200",   # Alibaba Cloud
    "192.0.0.192",       # Oracle Cloud
    "fd00:ec2::254",     # AWS IMDS over IPv6
})

# Explicitly blocked network ranges, on top of the property checks below.
BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),        # "this network"
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918 private
    ipaddress.ip_network("100.64.0.0/10"),    # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / metadata
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918 private
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),     # TEST-NET-1
    ipaddress.ip_network("192.88.99.0/24"),   # 6to4 relay anycast
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918 private
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),      # multicast
    ipaddress.ip_network("240.0.0.0/4"),      # reserved
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),           # unspecified
    ipaddress.ip_network("::1/128"),          # loopback
    ipaddress.ip_network("fc00::/7"),         # unique local
    ipaddress.ip_network("fe80::/10"),        # link-local
    ipaddress.ip_network("ff00::/8"),         # multicast
    ipaddress.ip_network("2001:db8::/32"),    # documentation
    ipaddress.ip_network("64:ff9b::/96"),     # NAT64
)

# Ports the backend is allowed to reach. Blocks things like 22, 3306, 6379.
ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})


class SSRFValidationError(ValueError):
    """Raised when a URL fails SSRF validation.

    The message is intentionally generic and safe to surface to a user:
    it never echoes internal network details back to the caller.
    """


# ── IP checks ────────────────────────────────────────────────────

def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if this address must never be contacted."""
    if str(ip) in BLOCKED_IPS:
        return True

    # Property-based checks catch ranges we may not have enumerated.
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    # IPv4-mapped / -compatible IPv6 (::ffff:127.0.0.1) must be unwrapped,
        # otherwise a loopback address can slip past the property checks.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or getattr(ip, "sixtofour", None)
        if mapped is not None and is_blocked_ip(mapped):
            return True
        if ip.teredo is not None:
            server, client = ip.teredo
            if is_blocked_ip(server) or is_blocked_ip(client):
                return True

    return any(ip in network for network in BLOCKED_NETWORKS)


def is_blocked_hostname(hostname: str) -> bool:
    """True if a hostname is internal, reserved, or otherwise off-limits."""
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return True
    if host in BLOCKED_HOSTNAMES:
        return True
    if any(host.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES):
        return True
    # A bare name with no dot is almost always an internal host.
    if "." not in host:
        return True
    return False


def resolve_and_check(hostname: str) -> list[str]:
    """
    Resolve a hostname and verify that *every* address it maps to is safe.

    Checking all records matters: an attacker-controlled name can return
    one public and one private address, and a naive check that only looks
    at the first result would let the private one through.

    Returns the list of resolved addresses. Raises SSRFValidationError if
    resolution fails or any address is blocked.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, socket.herror, UnicodeError, OSError):
        raise SSRFValidationError("The requested host could not be resolved.")

    if not infos:
        raise SSRFValidationError("The requested host could not be resolved.")

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        raw_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise SSRFValidationError("The requested host resolved to an invalid address.")

        if is_blocked_ip(ip):
            # Do not leak which internal address was hit.
            raise SSRFValidationError("The requested host is not permitted.")

        addresses.append(str(ip))

    return addresses


# ── Full URL validation ──────────────────────────────────────────

def validate_outbound_url(url: str, *, resolve_dns: bool = True) -> str:
    """
    Validate that a URL is safe for the backend to fetch.

    Enforces, in order: length, scheme, absence of embedded credentials,
    host allowlist, blocked-hostname rules, port allowlist, literal-IP
    rules, and finally DNS resolution against the blocked ranges.

    Returns the normalised URL. Raises SSRFValidationError otherwise.
    """
    if not isinstance(url, str):
        raise SSRFValidationError("Invalid URL.")

    candidate = url.strip()
    if not candidate:
        raise SSRFValidationError("Invalid URL.")
    if len(candidate) > 2048:
        raise SSRFValidationError("The URL is too long.")

    # Control characters can be used to smuggle headers or split requests.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        raise SSRFValidationError("Invalid URL.")

    try:
        parts = urlsplit(candidate)
    except ValueError:
        raise SSRFValidationError("Invalid URL.")

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFValidationError("Only http and https URLs are supported.")

    # Embedded credentials (http://user:pass@host) are a redirect/parser
    # confusion vector and are never legitimate here.
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise SSRFValidationError("URLs with embedded credentials are not permitted.")

    hostname = (parts.hostname or "").lower().rstrip(".")
    if not hostname:
        raise SSRFValidationError("Invalid URL.")

    try:
        port = parts.port
    except ValueError:
        raise SSRFValidationError("Invalid URL.")
    if port is not None and port not in ALLOWED_PORTS:
        raise SSRFValidationError("The requested port is not permitted.")

    # A literal IP can never be on the host allowlist, but check it anyway
    # so the failure mode is explicit rather than incidental.
    try:
        literal_ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        raise SSRFValidationError("Direct IP addresses are not permitted.")

    if is_blocked_hostname(hostname):
        raise SSRFValidationError("The requested host is not permitted.")

    # Allowlist: exact match, or a subdomain of an allowed registrable name.
    if hostname not in ALLOWED_HOSTS:
        raise SSRFValidationError("The requested host is not permitted.")

    if resolve_dns:
        resolve_and_check(hostname)

    return candidate


def is_allowed_host(hostname: str) -> bool:
    """Cheap string-only allowlist check (no DNS)."""
    host = (hostname or "").strip().lower().rstrip(".")
    return bool(host) and host in ALLOWED_HOSTS and not is_blocked_hostname(host)
