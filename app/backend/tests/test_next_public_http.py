import gzip
import io
import os
import socket
import sys
import unittest
import zlib
from unittest.mock import MagicMock, patch

import dns.exception
import dns.resolver


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)


from app.backend.next_public_http import PublicHttpError
from app.backend.next_public_http import PublicUrlTarget
from app.backend.next_public_http import ResolvedAddress
from app.backend.next_public_http import _PinnedHttpConnection
from app.backend.next_public_http import _PinnedHttpsConnection
from app.backend.next_public_http import fetch_public_text
from app.backend.next_public_http import resolve_public_addresses
from app.backend.next_public_http import validate_public_url


PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"


def _address(ip=PUBLIC_IPV4):
    if ":" in ip:
        return ResolvedAddress(
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            (ip, 443, 0, 0),
            ip,
        )
    return ResolvedAddress(
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        (ip, 443),
        ip,
    )


def _resolver(hostname, port):
    address = _address()
    return (
        ResolvedAddress(
            address.family,
            address.socket_type,
            address.protocol,
            (address.ip, port),
            address.ip,
        ),
    )


class _Headers(dict):
    def get_content_charset(self):
        content_type = self.get("Content-Type", "")
        marker = "charset="
        return content_type.split(marker, 1)[1].split(";", 1)[0].strip() if marker in content_type else None


class _Response:
    def __init__(self, body=b"", *, status=200, headers=None):
        self.status = status
        self.headers = _Headers(headers or {})
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)


class _Socket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)


class _Connection:
    def __init__(self, response, *, request_log, target, address, timeout):
        self._response = response
        self._request_log = request_log
        self._target = target
        self._address = address
        self._timeout = timeout
        self.sock = _Socket()
        self.closed = False

    def request(self, method, path, headers=None):
        self._request_log.append(
            {
                "method": method,
                "path": path,
                "headers": dict(headers or {}),
                "target": self._target,
                "address": self._address,
                "timeout": self._timeout,
            }
        )

    def getresponse(self):
        return self._response

    def close(self):
        self.closed = True


def _factory(responses, request_log, connections=None):
    remaining = list(responses)

    def create(target, address, timeout):
        response = remaining.pop(0)
        if isinstance(response, BaseException):
            raise response
        connection = _Connection(
            response,
            request_log=request_log,
            target=target,
            address=address,
            timeout=timeout,
        )
        if connections is not None:
            connections.append(connection)
        return connection

    return create


class PublicUrlValidationTests(unittest.TestCase):
    def test_accepts_public_ipv4_and_ipv6_answers(self):
        for ip in (PUBLIC_IPV4, PUBLIC_IPV6):
            with self.subTest(ip=ip):
                with patch(
                    "app.backend.next_public_http._query_dns_record",
                    side_effect=lambda _hostname, record_type, _lifetime: (
                        (ip,)
                        if (record_type == "AAAA") == (":" in ip)
                        else ()
                    ),
                ):
                    validate_public_url("https://shop.example/product")

    def test_rejects_every_non_public_address_class(self):
        blocked = (
            "0.0.0.0",
            "10.0.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "192.168.1.10",
            "224.0.0.1",
            "240.0.0.1",
            "::",
            "::1",
            "::ffff:127.0.0.1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
        )
        for ip in blocked:
            with self.subTest(ip=ip):
                with patch(
                    "app.backend.next_public_http._query_dns_record",
                    side_effect=lambda _hostname, record_type, _lifetime: (
                        (ip,)
                        if (record_type == "AAAA") == (":" in ip)
                        else ()
                    ),
                ):
                    with self.assertRaisesRegex(PublicHttpError, "^url_blocked$"):
                        validate_public_url("https://shop.example/product")

    def test_rejects_mixed_public_and_private_dns_answers(self):
        with patch(
            "app.backend.next_public_http._query_dns_record",
            side_effect=[(PUBLIC_IPV4, "10.0.0.8"), ()],
        ):
            with self.assertRaisesRegex(PublicHttpError, "^url_blocked$"):
                validate_public_url("https://shop.example/product")

    def test_rejects_alternate_numeric_host_when_resolver_maps_it_to_loopback(self):
        for host in ("2130706433", "017700000001", "0x7f000001"):
            with self.subTest(host=host):
                with patch(
                    "app.backend.next_public_http._query_dns_record",
                    side_effect=[("127.0.0.1",), ()],
                ):
                    with self.assertRaisesRegex(PublicHttpError, "^url_blocked$"):
                        validate_public_url(f"http://{host}/")

    def test_rejects_invalid_scheme_credentials_hostname_and_port(self):
        urls = (
            "file:///etc/passwd",
            "gopher://example.com/",
            "https://user@example.com/",
            "https://user:pass@example.com/",
            "https:///missing-host",
            "https://example.com:0/",
            "https://example.com:99999/",
            "https://example.com\\@127.0.0.1/",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(PublicHttpError, "^url_invalid$"):
                    validate_public_url(url, resolver=_resolver)

    def test_dns_failure_is_value_free(self):
        with patch(
            "app.backend.next_public_http._query_dns_record",
            side_effect=dns.resolver.NXDOMAIN,
        ):
            with self.assertRaises(PublicHttpError) as raised:
                resolve_public_addresses("shop.example", 443)
        self.assertEqual(str(raised.exception), "dns_failed")
        self.assertNotIn("sensitive", str(raised.exception))

    def test_dns_resolution_obeys_validation_timeout(self):
        with patch(
            "app.backend.next_public_http._query_dns_record",
            side_effect=dns.exception.Timeout,
        ):
            with self.assertRaisesRegex(PublicHttpError, "^timeout$"):
                validate_public_url(
                    "https://shop.example/product",
                    timeout_seconds=0.001,
                )

    def test_one_dns_family_failure_does_not_discard_public_other_family(self):
        cases = (
            ("A", PUBLIC_IPV4, dns.exception.Timeout),
            ("AAAA", PUBLIC_IPV6, dns.resolver.NoNameservers),
        )
        for successful_type, expected_ip, failure in cases:
            with self.subTest(successful_type=successful_type):
                def query(_hostname, record_type, _lifetime):
                    if record_type == successful_type:
                        return (expected_ip,)
                    raise failure

                with patch(
                    "app.backend.next_public_http._query_dns_record",
                    side_effect=query,
                ):
                    addresses = resolve_public_addresses("shop.example", 443)

                self.assertEqual([address.ip for address in addresses], [expected_ip])


class PublicHttpTransportTests(unittest.TestCase):
    def test_fetch_uses_validated_address_and_original_host_header(self):
        requests = []
        response = _Response(b"price", headers={"Content-Type": "text/plain; charset=utf-8"})

        value = fetch_public_text(
            "https://shop.example:8443/product?id=1#ignored",
            resolver=_resolver,
            connection_factory=_factory([response], requests),
        )

        self.assertEqual(value, "price")
        self.assertEqual(requests[0]["address"].ip, PUBLIC_IPV4)
        self.assertEqual(requests[0]["target"].hostname, "shop.example")
        self.assertEqual(requests[0]["headers"]["Host"], "shop.example:8443")
        self.assertEqual(requests[0]["path"], "/product?id=1")

    def test_relative_redirect_is_revalidated_and_followed(self):
        requests = []
        resolved = []

        def resolver(hostname, port):
            resolved.append((hostname, port))
            return _resolver(hostname, port)

        value = fetch_public_text(
            "https://shop.example/old",
            resolver=resolver,
            connection_factory=_factory(
                [
                    _Response(status=302, headers={"Location": "/new"}),
                    _Response(b"ok"),
                ],
                requests,
            ),
        )

        self.assertEqual(value, "ok")
        self.assertEqual(resolved, [("shop.example", 443), ("shop.example", 443)])
        self.assertEqual([item["path"] for item in requests], ["/old", "/new"])

    def test_redirect_to_private_target_is_blocked_before_second_connection(self):
        requests = []

        def resolver(hostname, port):
            if hostname == "internal.example":
                raise PublicHttpError("url_blocked")
            return _resolver(hostname, port)

        with self.assertRaisesRegex(PublicHttpError, "^url_blocked$"):
            fetch_public_text(
                "https://shop.example/product",
                resolver=resolver,
                connection_factory=_factory(
                    [_Response(status=302, headers={"Location": "http://internal.example/admin"})],
                    requests,
                ),
            )
        self.assertEqual(len(requests), 1)

    def test_redirect_loop_and_limit_are_bounded(self):
        with self.assertRaisesRegex(PublicHttpError, "^redirect_loop$"):
            fetch_public_text(
                "https://shop.example/a",
                resolver=_resolver,
                connection_factory=_factory(
                    [
                        _Response(status=302, headers={"Location": "/b"}),
                        _Response(status=302, headers={"Location": "/a"}),
                    ],
                    [],
                ),
            )
        with self.assertRaisesRegex(PublicHttpError, "^redirect_limit$"):
            fetch_public_text(
                "https://shop.example/a",
                maximum_redirects=1,
                resolver=_resolver,
                connection_factory=_factory(
                    [
                        _Response(status=302, headers={"Location": "/b"}),
                        _Response(status=302, headers={"Location": "/c"}),
                    ],
                    [],
                ),
            )

    def test_declared_and_streamed_oversized_responses_are_rejected(self):
        for response in (
            _Response(b"", headers={"Content-Length": "9"}),
            _Response(b"123456789"),
        ):
            with self.subTest(headers=response.headers):
                with self.assertRaisesRegex(PublicHttpError, "^response_too_large$"):
                    fetch_public_text(
                        "https://shop.example/product",
                        maximum_wire_bytes=8,
                        resolver=_resolver,
                        connection_factory=_factory([response], []),
                    )

    def test_gzip_and_deflate_decoding_are_bounded(self):
        payload = b"x" * 33
        for encoding, encoded in (
            ("gzip", gzip.compress(payload)),
            ("deflate", zlib.compress(payload)),
        ):
            with self.subTest(encoding=encoding):
                with self.assertRaisesRegex(PublicHttpError, "^response_too_large$"):
                    fetch_public_text(
                        "https://shop.example/product",
                        maximum_decoded_bytes=32,
                        resolver=_resolver,
                        connection_factory=_factory(
                            [_Response(encoded, headers={"Content-Encoding": encoding})],
                            [],
                        ),
                    )

    def test_successfully_decodes_gzip_with_declared_charset(self):
        payload = "prijs € 12,50".encode("utf-8")
        value = fetch_public_text(
            "https://shop.example/product",
            resolver=_resolver,
            connection_factory=_factory(
                [
                    _Response(
                        gzip.compress(payload),
                        headers={
                            "Content-Encoding": "gzip",
                            "Content-Type": "text/html; charset=utf-8",
                        },
                    )
                ],
                [],
            ),
        )
        self.assertEqual(value, "prijs € 12,50")

    def test_rejects_stacked_content_encodings(self):
        with self.assertRaisesRegex(
            PublicHttpError,
            "^content_encoding_unsupported$",
        ):
            fetch_public_text(
                "https://shop.example/product",
                resolver=_resolver,
                connection_factory=_factory(
                    [
                        _Response(
                            gzip.compress(b"payload"),
                            headers={"Content-Encoding": "gzip, br"},
                        )
                    ],
                    [],
                ),
            )

    def test_timeout_and_http_errors_are_value_free(self):
        with self.assertRaisesRegex(PublicHttpError, "^timeout$"):
            fetch_public_text(
                "https://shop.example/product",
                resolver=_resolver,
                connection_factory=_factory([socket.timeout("private detail")], []),
            )
        with self.assertRaisesRegex(PublicHttpError, "^http_error$"):
            fetch_public_text(
                "https://shop.example/product",
                resolver=_resolver,
                connection_factory=_factory([_Response(b"secret", status=500)], []),
            )

    def test_connections_are_closed_after_success_and_failure(self):
        connections = []
        fetch_public_text(
            "https://shop.example/product",
            resolver=_resolver,
            connection_factory=_factory([_Response(b"ok")], [], connections),
        )
        self.assertTrue(connections[0].closed)


class PinnedConnectionTests(unittest.TestCase):
    def _target(self, scheme):
        return PublicUrlTarget(
            url=f"{scheme}://shop.example/",
            scheme=scheme,
            hostname="shop.example",
            port=443 if scheme == "https" else 80,
            host_header="shop.example",
            request_target="/",
            addresses=(_address(),),
        )

    def test_http_connection_connects_to_resolved_address(self):
        raw_socket = MagicMock()
        address = _address()
        connection = _PinnedHttpConnection(self._target("http"), address, 2.0)
        with patch("app.backend.next_public_http._connect_socket", return_value=raw_socket) as connect:
            connection.connect()
        connect.assert_called_once_with(address, 2.0)
        self.assertIs(connection.sock, raw_socket)

    def test_https_connection_preserves_sni_hostname(self):
        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = wrapped_socket
        address = _address()
        with patch("app.backend.next_public_http.ssl.create_default_context", return_value=context):
            connection = _PinnedHttpsConnection(self._target("https"), address, 2.0)
        with patch("app.backend.next_public_http._connect_socket", return_value=raw_socket) as connect:
            connection.connect()
        connect.assert_called_once_with(address, 2.0)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="shop.example")
        self.assertIs(connection.sock, wrapped_socket)


if __name__ == "__main__":
    unittest.main()
