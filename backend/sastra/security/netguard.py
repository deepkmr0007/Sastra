"""Outbound network guard: SSRF protection, size/time limits, robots.txt.

Every server-side fetch of a user- or model-supplied URL goes through
``safe_fetch``. It resolves the host itself, rejects private/loopback/link-local
/metadata addresses, re-validates every redirect hop and caps body size.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass

USER_AGENT = "SASTRA-Research/1.0 (+personal research assistant; respects robots.txt)"
_BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


class NetworkPolicyError(Exception):
    pass


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
        or (addr.version == 6 and addr.ipv4_mapped is not None and not _ip_is_public(str(addr.ipv4_mapped)))
    )


def validate_url(url: str, allow_private: bool = False) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise NetworkPolicyError(f"Scheme not allowed: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise NetworkPolicyError("URL has no host")
    if parsed.username or parsed.password:
        raise NetworkPolicyError("Credentials in URLs are not allowed")
    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        raise NetworkPolicyError(f"Host not allowed: {host}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443, 8080, 8443) and not allow_private:
        raise NetworkPolicyError(f"Port not allowed: {port}")
    if not allow_private:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as e:
            raise NetworkPolicyError(f"Cannot resolve host: {host}") from e
        for info in infos:
            ip = info[4][0]
            if not _ip_is_public(ip):
                raise NetworkPolicyError(f"Host resolves to a non-public address: {host} -> {ip}")
    return parsed


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def _robots_allows(url: str, timeout: int) -> bool:
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        validate_url(robots_url)
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with _opener.open(req, timeout=timeout) as resp:
            raw = resp.read(200_000).decode("utf-8", "replace")
        rp.parse(raw.splitlines())
    except Exception:
        return True  # unreachable robots.txt = no stated restriction
    return rp.can_fetch(USER_AGENT, url)


def safe_fetch(url: str, *, max_bytes: int = 3_000_000, timeout: int = 20, max_redirects: int = 5,
               respect_robots: bool = True, allow_private: bool = False) -> FetchResult:
    current = url
    for _ in range(max_redirects + 1):
        validate_url(current, allow_private=allow_private)
        if respect_robots and not allow_private and not _robots_allows(current, timeout):
            raise NetworkPolicyError(f"robots.txt disallows fetching {current}")
        req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,text/plain,*/*;q=0.5"})
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise NetworkPolicyError("Redirect without Location") from e
                current = urllib.parse.urljoin(current, loc)
                continue
            body = e.read(min(max_bytes, 50_000)) if hasattr(e, "read") else b""
            return FetchResult(url, current, e.code, e.headers.get("Content-Type", ""), body, False)
        with resp:
            body = resp.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            return FetchResult(url, current, resp.status, resp.headers.get("Content-Type", ""), body[:max_bytes], truncated)
    raise NetworkPolicyError("Too many redirects")
