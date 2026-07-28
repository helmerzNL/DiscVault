"""Bounded HTTP transport for user-controlled public URLs."""

from __future__ import annotations

import gzip
import http.client
import io
import ipaddress
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import dns.exception
import dns.resolver


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_REDIRECTS = 5
# Single source of truth for the outbound browser User-Agent.
#
# Shops reject stale Chrome versions: Amazon started serving its "automated
# access to Amazon data" wall to Chrome/124 while accepting 126+, which silently
# broke price extraction. Keep this reasonably current and bump it here only —
# every price provider imports this value rather than pinning its own.
DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_MAX_WIRE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DECODED_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_ALLOWED_METHODS = frozenset({"GET", "POST"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_BLOCKED_REQUEST_HEADERS = frozenset({"connection", "content-length", "host", "transfer-encoding"})
PUBLIC_HTTP_FAILURE_CODES = frozenset(
    {
        "content_decode_failed",
        "content_encoding_unsupported",
        "dns_failed",
        "http_error",
        "network_error",
        "redirect_invalid",
        "redirect_limit",
        "redirect_loop",
        "request_too_large",
        "response_too_large",
        "timeout",
        "url_blocked",
        "url_invalid",
    }
)


class PublicHttpError(RuntimeError):
    """Stable, value-free outbound HTTP failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedAddress:
    family: int
    socket_type: int
    protocol: int
    socket_address: tuple[Any, ...]
    ip: str


@dataclass(frozen=True)
class PublicUrlTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    host_header: str
    request_target: str
    addresses: tuple[ResolvedAddress, ...]


Resolver = Callable[[str, int], tuple[ResolvedAddress, ...]]
ConnectionFactory = Callable[[PublicUrlTarget, ResolvedAddress, float], http.client.HTTPConnection]


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicHttpError("timeout")
    return remaining


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _query_dns_record(hostname: str, record_type: str, lifetime: float) -> tuple[str, ...]:
    answer = dns.resolver.resolve(
        hostname,
        record_type,
        lifetime=lifetime,
        search=True,
    )
    return tuple(str(record) for record in answer)


def resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[ResolvedAddress, ...]:
    """Resolve *hostname* and fail unless every answer is globally routable."""

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        socket_address = (
            (str(literal), port, 0, 0)
            if family == socket.AF_INET6
            else (str(literal), port)
        )
        if not _is_public_address(str(literal)):
            raise PublicHttpError("url_blocked")
        return (
            ResolvedAddress(
                family=family,
                socket_type=socket.SOCK_STREAM,
                protocol=socket.IPPROTO_TCP,
                socket_address=socket_address,
                ip=str(literal),
            ),
        )

    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    records: list[tuple[int, str]] = []
    timeout_error: dns.exception.Timeout | None = None
    dns_error: dns.exception.DNSException | None = None
    for record_type, family in (("A", socket.AF_INET), ("AAAA", socket.AF_INET6)):
        try:
            answers = _query_dns_record(
                hostname,
                record_type,
                _remaining_seconds(deadline),
            )
        except dns.resolver.NoAnswer:
            continue
        except dns.exception.Timeout as exc:
            timeout_error = exc
            continue
        except dns.exception.DNSException as exc:
            dns_error = exc
            continue
        records.extend((family, answer) for answer in answers)

    if not records:
        if timeout_error is not None:
            raise PublicHttpError("timeout") from timeout_error
        if dns_error is not None:
            raise PublicHttpError("dns_failed") from dns_error

    addresses: list[ResolvedAddress] = []
    seen: set[tuple[int, str]] = set()
    for family, ip in records:
        if not _is_public_address(ip):
            raise PublicHttpError("url_blocked")
        key = (family, ip)
        if key in seen:
            continue
        seen.add(key)
        socket_address = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
        addresses.append(
            ResolvedAddress(
                family=family,
                socket_type=socket.SOCK_STREAM,
                protocol=socket.IPPROTO_TCP,
                socket_address=socket_address,
                ip=ip,
            )
        )

    if not addresses:
        raise PublicHttpError("dns_failed")
    return tuple(addresses)


def _parse_public_url(url: str) -> tuple[SplitResult, str, int]:
    text = str(url or "").strip()
    if not text or any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise PublicHttpError("url_invalid")
    if "\\" in text:
        raise PublicHttpError("url_invalid")
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise PublicHttpError("url_invalid") from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PublicHttpError("url_invalid")
    try:
        normalized_hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise PublicHttpError("url_invalid") from exc
    if not normalized_hostname:
        raise PublicHttpError("url_invalid")
    if port is not None and not 1 <= port <= 65535:
        raise PublicHttpError("url_invalid")
    return parsed, normalized_hostname, port if port is not None else (443 if scheme == "https" else 80)


def _host_header(hostname: str, port: int, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _prepare_target(url: str, resolver: Resolver) -> PublicUrlTarget:
    parsed, hostname, port = _parse_public_url(url)
    addresses = resolver(hostname, port)
    path = parsed.path or "/"
    request_target = urlunsplit(("", "", path, parsed.query, ""))
    return PublicUrlTarget(
        url=urlunsplit((parsed.scheme.lower(), _host_header(hostname, port, parsed.scheme.lower()), path, parsed.query, "")),
        scheme=parsed.scheme.lower(),
        hostname=hostname,
        port=port,
        host_header=_host_header(hostname, port, parsed.scheme.lower()),
        request_target=request_target,
        addresses=addresses,
    )


def _resolve_target(
    resolver: Resolver,
    hostname: str,
    port: int,
    deadline: float,
) -> tuple[ResolvedAddress, ...]:
    if resolver is resolve_public_addresses:
        return resolve_public_addresses(
            hostname,
            port,
            timeout_seconds=_remaining_seconds(deadline),
        )
    return resolver(hostname, port)


def validate_public_url(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resolver: Resolver | None = None,
) -> None:
    """Validate and resolve a URL without opening a network connection."""

    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise PublicHttpError("configuration_invalid") from exc
    if timeout <= 0:
        raise PublicHttpError("configuration_invalid")
    deadline = time.monotonic() + timeout
    resolve = resolver or resolve_public_addresses
    _prepare_target(
        url,
        lambda hostname, port: _resolve_target(resolve, hostname, port, deadline),
    )
    _remaining_seconds(deadline)


def public_url_hostname(url: str) -> str:
    """Return a normalized hostname for safe diagnostics, or an empty string."""

    try:
        _parsed, hostname, _port = _parse_public_url(url)
    except PublicHttpError:
        return ""
    return hostname


def _connect_socket(address: ResolvedAddress, timeout: float) -> socket.socket:
    sock = socket.socket(address.family, address.socket_type, address.protocol)
    try:
        sock.settimeout(timeout)
        sock.connect(address.socket_address)
    except BaseException:
        sock.close()
        raise
    return sock


class _PinnedHttpConnection(http.client.HTTPConnection):
    def __init__(self, target: PublicUrlTarget, address: ResolvedAddress, timeout: float) -> None:
        super().__init__(target.hostname, target.port, timeout=timeout)
        self._resolved_address = address

    def connect(self) -> None:
        self.sock = _connect_socket(self._resolved_address, float(self.timeout))


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, target: PublicUrlTarget, address: ResolvedAddress, timeout: float) -> None:
        super().__init__(
            target.hostname,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._resolved_address = address

    def connect(self) -> None:
        raw_socket = _connect_socket(self._resolved_address, float(self.timeout))
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _connection_for_target(
    target: PublicUrlTarget,
    address: ResolvedAddress,
    timeout: float,
) -> http.client.HTTPConnection:
    if target.scheme == "https":
        return _PinnedHttpsConnection(target, address, timeout)
    return _PinnedHttpConnection(target, address, timeout)


def _request_headers(headers: Mapping[str, str] | None, host_header: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()
        if (
            not name
            or name.lower() in _BLOCKED_REQUEST_HEADERS
            or "\r" in name
            or "\n" in name
            or "\r" in value
            or "\n" in value
        ):
            continue
        result[name] = value
    result["Host"] = host_header
    result["Connection"] = "close"
    return result


def _read_wire_body(
    response: http.client.HTTPResponse,
    connection: http.client.HTTPConnection,
    *,
    deadline: float,
    maximum_bytes: int,
) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = -1
        if declared_length > maximum_bytes:
            raise PublicHttpError("response_too_large")

    body = bytearray()
    while True:
        remaining = _remaining_seconds(deadline)
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        chunk = response.read(min(_READ_CHUNK_BYTES, maximum_bytes - len(body) + 1))
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise PublicHttpError("response_too_large")
    return bytes(body)


def _bounded_gzip_decode(value: bytes, maximum_bytes: int) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(value), mode="rb") as stream:
            decoded = stream.read(maximum_bytes + 1)
    except (EOFError, OSError, zlib.error) as exc:
        raise PublicHttpError("content_decode_failed") from exc
    if len(decoded) > maximum_bytes:
        raise PublicHttpError("response_too_large")
    return decoded


def _bounded_deflate_decode(value: bytes, maximum_bytes: int) -> bytes:
    last_error: zlib.error | None = None
    for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decompressor = zlib.decompressobj(window_bits)
            decoded = decompressor.decompress(value, maximum_bytes + 1)
            if len(decoded) <= maximum_bytes:
                decoded += decompressor.flush(maximum_bytes - len(decoded) + 1)
            if len(decoded) > maximum_bytes or decompressor.unconsumed_tail:
                raise PublicHttpError("response_too_large")
            if not decompressor.eof:
                raise PublicHttpError("content_decode_failed")
            return decoded
        except zlib.error as exc:
            last_error = exc
    raise PublicHttpError("content_decode_failed") from last_error


def _decode_content(value: bytes, encoding: str, maximum_bytes: int) -> bytes:
    encodings = [item.strip().lower() for item in encoding.split(",") if item.strip()]
    if len(encodings) > 1:
        raise PublicHttpError("content_encoding_unsupported")
    normalized = encodings[0] if encodings else ""
    if not normalized or normalized == "identity":
        if len(value) > maximum_bytes:
            raise PublicHttpError("response_too_large")
        return value
    if normalized in {"gzip", "x-gzip"}:
        return _bounded_gzip_decode(value, maximum_bytes)
    if normalized == "deflate":
        return _bounded_deflate_decode(value, maximum_bytes)
    raise PublicHttpError("content_encoding_unsupported")


def _perform_request(
    target: PublicUrlTarget,
    *,
    method: str,
    body: bytes | None,
    headers: Mapping[str, str] | None,
    deadline: float,
    maximum_bytes: int,
    connection_factory: ConnectionFactory,
) -> tuple[int, bytes, Mapping[str, str], str | None]:
    last_error: BaseException | None = None
    addresses = target.addresses if method == "GET" else target.addresses[:1]
    for address in addresses:
        connection: http.client.HTTPConnection | None = None
        try:
            connection = connection_factory(target, address, _remaining_seconds(deadline))
            connection.request(
                method,
                target.request_target,
                body=body,
                headers=_request_headers(headers, target.host_header),
            )
            response = connection.getresponse()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            charset = response.headers.get_content_charset()
            response_body = _read_wire_body(
                response,
                connection,
                deadline=deadline,
                maximum_bytes=maximum_bytes,
            )
            return int(response.status), response_body, response_headers, charset
        except PublicHttpError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError) as exc:
            last_error = exc
        finally:
            if connection is not None:
                connection.close()
    if isinstance(last_error, (TimeoutError, socket.timeout)):
        raise PublicHttpError("timeout") from last_error
    raise PublicHttpError("network_error") from last_error


def request_public_text(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    maximum_redirects: int = DEFAULT_MAX_REDIRECTS,
    maximum_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    maximum_wire_bytes: int = DEFAULT_MAX_WIRE_BYTES,
    maximum_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    resolver: Resolver | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> str:
    """Request text from a public URL using DNS-pinned connections."""

    try:
        timeout = float(timeout_seconds)
        redirect_limit = int(maximum_redirects)
        request_limit = int(maximum_request_bytes)
        wire_limit = int(maximum_wire_bytes)
        decoded_limit = int(maximum_decoded_bytes)
    except (TypeError, ValueError) as exc:
        raise PublicHttpError("configuration_invalid") from exc
    normalized_method = str(method or "").strip().upper()
    if (
        timeout <= 0
        or redirect_limit < 0
        or request_limit <= 0
        or wire_limit <= 0
        or decoded_limit <= 0
        or normalized_method not in _ALLOWED_METHODS
        or (body is not None and not isinstance(body, bytes))
        or (normalized_method == "GET" and body is not None)
        or (normalized_method == "POST" and redirect_limit != 0)
    ):
        raise PublicHttpError("configuration_invalid")
    if body is not None and len(body) > request_limit:
        raise PublicHttpError("request_too_large")

    resolve = resolver or resolve_public_addresses
    connect = connection_factory or _connection_for_target
    deadline = time.monotonic() + timeout
    current_url = str(url or "").strip()
    visited: set[str] = set()

    for redirect_count in range(redirect_limit + 1):
        target = _prepare_target(
            current_url,
            lambda hostname, port: _resolve_target(resolve, hostname, port, deadline),
        )
        if target.url in visited:
            raise PublicHttpError("redirect_loop")
        visited.add(target.url)

        status, wire_body, response_headers, charset = _perform_request(
            target,
            method=normalized_method,
            body=body,
            headers=headers,
            deadline=deadline,
            maximum_bytes=wire_limit,
            connection_factory=connect,
        )
        if status in _REDIRECT_STATUSES:
            location = response_headers.get("location", "").strip()
            if not location:
                raise PublicHttpError("redirect_invalid")
            if redirect_count >= redirect_limit:
                raise PublicHttpError("redirect_limit")
            current_url = urljoin(target.url, location)
            continue
        if status >= 400:
            raise PublicHttpError("http_error")

        decoded = _decode_content(
            wire_body,
            response_headers.get("content-encoding", ""),
            decoded_limit,
        )
        try:
            return decoded.decode(charset or "utf-8", errors="replace")
        except LookupError as exc:
            raise PublicHttpError("content_decode_failed") from exc

    raise PublicHttpError("redirect_limit")


def fetch_public_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    maximum_redirects: int = DEFAULT_MAX_REDIRECTS,
    maximum_wire_bytes: int = DEFAULT_MAX_WIRE_BYTES,
    maximum_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    resolver: Resolver | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> str:
    """Fetch text from a public URL using a DNS-pinned GET request."""

    return request_public_text(
        url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        maximum_redirects=maximum_redirects,
        maximum_wire_bytes=maximum_wire_bytes,
        maximum_decoded_bytes=maximum_decoded_bytes,
        resolver=resolver,
        connection_factory=connection_factory,
    )
