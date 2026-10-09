from __future__ import annotations

from email.message import Message
from http.client import HTTPException, HTTPMessage, HTTPResponse
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request


ROOT = Path(__file__).resolve().parents[1]


def load_source(relative: str, *, root: Path = ROOT) -> ModuleType:
    name = relative.removesuffix(".py").replace("/", ".")
    spec = importlib.util.spec_from_file_location(name, root / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load source for HTTP ownership regression")
    module = importlib.util.module_from_spec(spec)
    replacements = {name: module}
    if Path(relative).name in {"audit_freshness.py", "audit_official_docs.py"}:
        sibling = (Path(relative).parent / "sync_action_pins.py").as_posix()
        replacements["sync_action_pins"] = load_source(sibling, root=root)
    previous = {key: sys.modules[key] for key in replacements if key in sys.modules}
    try:
        sys.modules.update(replacements)
        spec.loader.exec_module(module)
    finally:
        for key in replacements:
            if key in previous:
                sys.modules[key] = previous[key]
            else:
                sys.modules.pop(key, None)
    return module


def invoke_boundary(
    relative: str, kind: str, failure: Exception, *, root: Path = ROOT
) -> None:
    module = load_source(relative, root=root)

    def reject_response(request: object, **kwargs: object) -> None:
        del kwargs
        request_url = getattr(request, "full_url", None)
        expected_host = (
            "pypi.org"
            if kind == "freshness"
            else "docs.github.com"
            if kind == "docs"
            else "registry.npmjs.org"
            if kind == "toolchain-npm"
            else "api.github.com"
        )
        if (
            not isinstance(request_url, str)
            or urlsplit(request_url).hostname != expected_host
        ):
            raise AssertionError(
                "Synthetic response is not bound to the expected upstream host"
            )
        raise failure

    opener = mock.Mock(side_effect=reject_response)
    if kind == "freshness":
        with mock.patch.object(module.PYPI_OPENER, "open", opener):
            module.read_json("https://pypi.org/pypi/synthetic/json")
    elif kind == "docs":
        with mock.patch.object(
            module, "build_opener", return_value=mock.Mock(open=opener)
        ):
            module.read_document(
                "https://docs.github.com/en/rest/git/trees", ("docs.github.com",)
            )
    elif kind == "community":
        with mock.patch.object(module.GITHUB_API_OPENER, "open", opener):
            module.GitHubClient("synthetic-not-a-credential").get_json(
                "repos/synthetic/target/community/profile"
            )
    elif kind.startswith("toolchain"):
        policy = module.load_policy(root / ".github/ci-toolchain.json")
        if kind == "toolchain-github":
            module.fetch_latest_release(policy.tools[0], opener=opener)
        else:
            module.fetch_latest_npm_tool(policy.npm_tools[0], opener=opener)
    else:
        module.GitHubReleaseClient(
            "synthetic-not-a-credential", opener=opener
        ).get_json("/repos/synthetic/target")


BOUNDARIES = (
    ("scripts/audit_freshness.py", "freshness"),
    ("skills/repo-scaffold/scripts/audit_freshness.py", "freshness"),
    ("scripts/audit_official_docs.py", "docs"),
    ("skills/repo-scaffold/scripts/check_community_health.py", "community"),
    ("skills/repo-scaffold/scripts/ci_toolchain.py", "toolchain-github"),
    ("skills/repo-scaffold/scripts/ci_toolchain.py", "toolchain-npm"),
    ("scripts/sync_action_pins.py", "action-pins"),
    ("skills/repo-scaffold/scripts/sync_action_pins.py", "action-pins"),
)

REDIRECT_BOUNDARIES = tuple(
    (relative, kind) for relative, kind in BOUNDARIES if kind != "toolchain-npm"
) + (("scripts/check_code_scanning_alerts.py", "gate"),)


def load_redirect_handler(relative: str, kind: str) -> HTTPRedirectHandler:
    module = load_source(relative)
    if kind == "docs":
        return module.ApprovedRedirectHandler(("docs.github.com",))
    return module.RejectRedirectHandler()


FRAMING_BOUNDARIES = BOUNDARIES + (("scripts/check_code_scanning_alerts.py", "gate"),)


def invoke_response(module: ModuleType, kind: str, response: object) -> object:
    opener = mock.Mock(return_value=response)
    if kind == "freshness":
        with mock.patch.object(module.PYPI_OPENER, "open", opener):
            return module.read_json("https://pypi.org/pypi/synthetic/json")
    if kind == "docs":
        with mock.patch.object(
            module, "build_opener", return_value=mock.Mock(open=opener)
        ):
            return module.read_document(
                "https://docs.github.com/en/rest/git/trees", ("docs.github.com",)
            )
    if kind == "community":
        with mock.patch.object(module.GITHUB_API_OPENER, "open", opener):
            return module.GitHubClient().get_json(
                "repos/synthetic/target/community/profile"
            )
    if kind.startswith("toolchain"):
        policy = module.load_policy(ROOT / ".github/ci-toolchain.json")
        if kind == "toolchain-github":
            return module.fetch_latest_release(policy.tools[0], opener=opener)
        return module.fetch_latest_npm_tool(policy.npm_tools[0], opener=opener)
    if kind == "gate":
        with mock.patch.object(module.GITHUB_API_OPENER, "open", opener):
            return module.api_json(
                "https://api.github.com/repos/synthetic/target",
                "synthetic-not-a-credential",
            )
    return module.GitHubReleaseClient(
        "synthetic-not-a-credential", opener=opener
    ).get_json("/repos/synthetic/target")


def native_response(body: bytes, headers: bytes) -> HTTPResponse:
    wire = BytesIO(
        b"HTTP/1.1 200 OK\r\nConnection: close\r\n" + headers + b"\r\n" + body
    )
    response = HTTPResponse(mock.Mock(makefile=mock.Mock(return_value=wire)))
    response.begin()
    response.url = "https://docs.github.com/en/rest/git/trees"  # type: ignore[attr-defined]
    return response


class HttpFramingCompletenessTests(unittest.TestCase):
    def test_gate_counts_payload_before_rejecting_incomplete_native_framing(
        self,
    ) -> None:
        module = load_source("scripts/check_code_scanning_alerts.py")
        body = b'{"synthetic":true}'
        response = native_response(body, b"Content-Length: 35\r\n")
        try:
            with module.inspection_budget() as budget:
                with self.assertRaisesRegex(module.GateError, "incomplete"):
                    invoke_response(module, "gate", response)
                self.assertEqual(budget.response_bytes, len(body))
                self.assertEqual(budget.requests, 1)
            self.assertTrue(response.isclosed())
        finally:
            response.close()

    def test_raw_content_length_grammar_and_identical_values(self) -> None:
        body = b'{"synthetic":true}'
        cases = (
            (b"Content-Length: invalid\r\n", False),
            (b"Content-Length: -1\r\n", False),
            (b"Content-Length: +18\r\n", False),
            (b"Content-Length: 18.0\r\n", False),
            (b"Content-Length: \r\n", False),
            (b"Content-Length: 18,\r\n", False),
            (b"Content-Length: 18,35\r\n", False),
            (b"Content-Length: 18\r\nContent-Length: 35\r\n", False),
            (b"Content-Length: 18\r\n", True),
            (b"Content-Length: 18\r\nContent-Length: 18\r\n", True),
            (b"Content-Length: 18, 18\r\n", True),
            (b"Content-Length: 00018\r\nContent-Length: 18\r\n", True),
            (b"Content-Length: 00018,18\r\n", True),
        )
        for relative, kind in FRAMING_BOUNDARIES:
            for headers, accepted in cases:
                with self.subTest(source=relative, kind=kind, headers=headers):
                    module = load_source(relative)
                    response = native_response(body, headers)
                    try:
                        if accepted:
                            expected = (
                                (response.url, body.decode())
                                if kind == "docs"
                                else {"synthetic": True}
                            )  # type: ignore[attr-defined]
                            self.assertEqual(
                                invoke_response(module, kind, response), expected
                            )
                        else:
                            with mock.patch.object(
                                response,
                                "read",
                                side_effect=AssertionError(
                                    "invalid header must not reach body read"
                                ),
                            ):
                                with self.assertRaisesRegex(
                                    (RuntimeError, ValueError), "Content-Length"
                                ):
                                    invoke_response(module, kind, response)
                        self.assertTrue(response.isclosed())
                    finally:
                        response.close()

    def test_native_header_work_and_declared_size_are_bounded_before_read(self) -> None:
        headers = (
            b"Content-Length: " + b"0" * 8193 + b"\r\n",
            b"Content-Length: " + b"9" * 64 + b"\r\n",
            b"Content-Length: " + b",".join([b"18"] * 101) + b"\r\n",
            b"Content-Length: 99999999\r\n",
        )
        for relative, kind in FRAMING_BOUNDARIES:
            for header in headers:
                with self.subTest(source=relative, header_bytes=len(header)):
                    module = load_source(relative)
                    response = native_response(b'{"synthetic":true}', header)
                    try:
                        with mock.patch.object(
                            response,
                            "read",
                            side_effect=AssertionError(
                                "resource cap must precede body read"
                            ),
                        ):
                            with self.assertRaises((RuntimeError, ValueError)):
                                invoke_response(module, kind, response)
                        self.assertTrue(response.isclosed())
                    finally:
                        response.close()

    def test_length_parser_protocol_is_identical_in_independent_clients(self) -> None:
        for relative, kind in FRAMING_BOUNDARIES:
            with self.subTest(source=relative):
                module = load_source(relative)
                response = native_response(b"", b"Content-Length: 0\r\n")
                try:
                    self.assertEqual(module.read_http_payload(response, 16), b"")
                finally:
                    response.close()
                response = native_response(b'{"synthetic":true}', b"")
                try:
                    for _ in range(101):
                        response.headers.add_header("Content-Length", "18")
                    with self.assertRaisesRegex(ValueError, "header safety bound"):
                        module.read_http_payload(response, 32)
                finally:
                    response.close()

    def test_short_declared_body_is_not_a_complete_receipt(self) -> None:
        body = b'{"synthetic":true}'
        for relative, kind in FRAMING_BOUNDARIES:
            with self.subTest(source=relative, kind=kind):
                module = load_source(relative)
                response = native_response(
                    body, f"Content-Length: {len(body) + 17}\r\n".encode()
                )
                try:
                    with self.assertRaisesRegex(
                        (RuntimeError, ValueError), "incomplete"
                    ):
                        invoke_response(module, kind, response)
                    self.assertTrue(response.isclosed())
                    if kind == "gate":
                        self.assertIsNone(module.GATE_BUDGET.get())
                finally:
                    response.close()

    def test_complete_native_framing_preserves_all_document_boundaries(self) -> None:
        body = b'{"synthetic":true}'
        cases = (
            (body, f"Content-Length: {len(body)}\r\n".encode()),
            (body, b""),
            (
                f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n",
                b"Transfer-Encoding: chunked\r\n",
            ),
            (
                f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n",
                b"Transfer-Encoding: chunked\r\nContent-Length: 18\r\n",
            ),
        )
        for relative, kind in FRAMING_BOUNDARIES:
            for payload, headers in cases:
                with self.subTest(source=relative, kind=kind, headers=headers):
                    module = load_source(relative)
                    response = native_response(payload, headers)
                    try:
                        result = invoke_response(module, kind, response)
                        expected = (
                            (response.url, body.decode())
                            if kind == "docs"
                            else {"synthetic": True}
                        )  # type: ignore[attr-defined]
                        self.assertEqual(result, expected)
                        self.assertTrue(response.isclosed())
                    finally:
                        response.close()

    def test_incomplete_chunk_parser_failures_are_controlled_and_closed(self) -> None:
        body = b'{"synthetic":true}'
        payloads = (
            f"{len(body):x}\r\n".encode() + body + b"\r\n",
            b"not-hex\r\n" + body,
            f"{len(body) + 17:x}\r\n".encode() + body,
        )
        for relative, kind in FRAMING_BOUNDARIES:
            for payload in payloads:
                with self.subTest(source=relative, kind=kind, payload=payload):
                    module = load_source(relative)
                    response = native_response(
                        payload, b"Transfer-Encoding: chunked\r\n"
                    )
                    try:
                        with self.assertRaises((RuntimeError, ValueError)) as error:
                            invoke_response(module, kind, response)
                        self.assertNotIsInstance(error.exception, HTTPException)
                        self.assertTrue(response.isclosed())
                    finally:
                        response.close()

    def test_native_size_cap_and_injected_non_http_stream_applicability(self) -> None:
        for relative, kind in FRAMING_BOUNDARIES:
            with self.subTest(source=relative, kind=kind):
                module = load_source(relative)
                with mock.patch.object(module, "MAX_RESPONSE_BYTES", 16):
                    response = native_response(b"x" * 17, b"Content-Length: 17\r\n")
                    try:
                        with self.assertRaises((RuntimeError, ValueError)):
                            invoke_response(module, kind, response)
                        self.assertTrue(response.isclosed())
                    finally:
                        response.close()
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = b'{"synthetic":true}'
                response.geturl.return_value = (
                    "https://docs.github.com/en/rest/git/trees"
                )
                result = invoke_response(module, kind, response)
                expected = (
                    (response.geturl.return_value, '{"synthetic":true}')
                    if kind == "docs"
                    else {"synthetic": True}
                )
                self.assertEqual(result, expected)


class HttpErrorResourceOwnershipTests(unittest.TestCase):
    def test_source_loading_restores_modules_and_binds_the_actual_sibling(self) -> None:
        for relative, kind in BOUNDARIES:
            with self.subTest(source=relative, kind=kind):
                before = sys.modules.copy()
                module = load_source(relative)
                self.assertEqual(
                    sys.modules.get(module.__name__), before.get(module.__name__)
                )
                self.assertEqual(
                    sys.modules.get("sync_action_pins"),
                    before.get("sync_action_pins"),
                )
                if kind in {"freshness", "docs"}:
                    self.assertEqual(
                        Path(module.sync_action_pins.__file__).resolve(),
                        (ROOT / relative).with_name("sync_action_pins.py").resolve(),
                    )

    def test_close_failure_is_controlled_at_every_owned_response_boundary(self) -> None:
        for relative, kind in BOUNDARIES:
            stream = BytesIO(b"Synthetic response")
            failure = HTTPError(
                "https://api.github.com/synthetic", 503, "synthetic", Message(), stream
            )
            try:
                with (
                    self.subTest(source=relative, kind=kind),
                    mock.patch.object(
                        failure, "close", side_effect=OSError("synthetic close error")
                    ),
                ):
                    with self.assertRaises((RuntimeError, ValueError)) as raised:
                        invoke_boundary(relative, kind, failure)
                    expected = (
                        "ValueError"
                        if kind == "action-pins"
                        else "ToolchainError"
                        if kind.startswith("toolchain")
                        else "AuditError"
                    )
                    self.assertEqual(type(raised.exception).__name__, expected)
                    self.assertIn("could not be closed", str(raised.exception))
            finally:
                stream.close()

    def test_non_http_network_errors_do_not_assume_owned_response_streams(self) -> None:
        for relative, kind in BOUNDARIES:
            failure = URLError("synthetic unavailable upstream")
            with (
                self.subTest(source=relative, kind=kind),
                self.assertRaises((RuntimeError, ValueError)) as raised,
            ):
                invoke_boundary(relative, kind, failure)
            expected = (
                "ValueError"
                if kind == "action-pins"
                else "ToolchainError"
                if kind.startswith("toolchain")
                else "AuditError"
            )
            self.assertEqual(type(raised.exception).__name__, expected)

    def test_owned_http_error_stream_closes_at_each_source_and_mirror_boundary(
        self,
    ) -> None:
        for relative, kind in BOUNDARIES:
            for status in (403, 429, 503):
                stream = BytesIO(b"Synthetic response")
                failure = HTTPError(
                    "https://api.github.com/synthetic",
                    status,
                    "synthetic",
                    Message(),
                    stream,
                )
                try:
                    with self.subTest(source=relative, kind=kind, status=status):
                        with self.assertRaises((RuntimeError, ValueError)) as raised:
                            invoke_boundary(relative, kind, failure)
                        self.assertNotIsInstance(raised.exception, HTTPError)
                        expected = (
                            "ValueError"
                            if kind == "action-pins"
                            else "ToolchainError"
                            if kind.startswith("toolchain")
                            else "AuditError"
                        )
                        self.assertEqual(type(raised.exception).__name__, expected)
                        self.assertTrue(stream.closed)
                finally:
                    stream.close()


class RedirectResourceOwnershipTests(unittest.TestCase):
    def test_approved_redirect_keeps_no_request_disposition(self) -> None:
        module = load_source("scripts/audit_official_docs.py")
        handler = module.ApprovedRedirectHandler(("docs.github.com",))
        with mock.patch.object(
            module.HTTPRedirectHandler, "redirect_request", return_value=None
        ):
            self.assertIsNone(
                handler.redirect_request(
                    Request("https://docs.github.com/source"),
                    None,
                    308,
                    "Redirect",
                    HTTPMessage(),
                    "https://docs.github.com/target",
                )
            )

    def test_approved_308_preserves_supported_methods_and_rejects_unsafe_methods(
        self,
    ) -> None:
        module = load_source("scripts/audit_official_docs.py")
        for method in ("GET", "HEAD", "POST", "PUT", "DELETE"):
            handler = module.ApprovedRedirectHandler(("docs.github.com",))
            request = Request("https://docs.github.com/source", method=method)
            with BytesIO() as response, self.subTest(method=method):
                if method in {"GET", "HEAD"}:
                    redirected = handler.redirect_request(
                        request,
                        response,
                        308,
                        "Redirect",
                        HTTPMessage(),
                        "https://docs.github.com/target",
                    )
                    self.assertIsNotNone(redirected)
                    assert redirected is not None
                    self.assertEqual(redirected.get_method(), method)
                else:
                    with self.assertRaises(HTTPError) as raised:
                        handler.redirect_request(
                            request,
                            response,
                            308,
                            "Redirect",
                            HTTPMessage(),
                            "https://docs.github.com/target",
                        )
                    self.assertEqual(raised.exception.code, 308)
                    raised.exception.close()

    def test_malformed_redirect_locations_close_before_controlled_refusal(self) -> None:
        for relative, kind in REDIRECT_BOUNDARIES:
            for location in (
                "https://[",
                "https://[invalid]/x",
                "https://docs.github.com:bad/x",
            ):
                with self.subTest(source=relative, location=location):
                    handler = load_redirect_handler(relative, kind)
                    parent = mock.Mock()
                    handler.add_parent(parent)
                    headers = HTTPMessage()
                    headers["Location"] = location
                    with BytesIO(b"Unused body") as response:
                        with self.assertRaises((RuntimeError, ValueError)) as error:
                            handler.http_error_302(
                                Request("https://api.github.com/synthetic"),
                                response,
                                302,
                                "Found",
                                headers,
                            )
                        self.assertTrue(response.closed)
                        self.assertNotIsInstance(error.exception, HTTPError)
                    parent.open.assert_not_called()

    def test_denied_redirects_close_without_reading_or_forwarding(self) -> None:
        for relative, kind in REDIRECT_BOUNDARIES:
            for status in (301, 302, 303, 307, 308):
                with self.subTest(source=relative, status=status):
                    handler = load_redirect_handler(relative, kind)
                    parent = mock.Mock()
                    handler.add_parent(parent)
                    headers = HTTPMessage()
                    headers["Location"] = "https://redirect.invalid/path"
                    request = Request(
                        "https://api.github.com/synthetic",
                        headers={"Authorization": "Bearer synthetic-not-a-credential"},
                    )
                    with BytesIO(b"Unused redirect body") as response:
                        with mock.patch.object(
                            response,
                            "read",
                            side_effect=AssertionError("must not drain"),
                        ):
                            with self.assertRaises((RuntimeError, ValueError)):
                                getattr(handler, f"http_error_{status}")(
                                    request, response, status, "Redirect", headers
                                )
                        self.assertTrue(response.closed)
                    parent.open.assert_not_called()

    def test_redirect_cleanup_failure_remains_a_domain_error(self) -> None:
        for relative, kind in REDIRECT_BOUNDARIES:
            with self.subTest(source=relative):
                handler = load_redirect_handler(relative, kind)
                headers = HTTPMessage()
                headers["Location"] = "https://redirect.invalid/path"
                with BytesIO(b"Unused redirect body") as response:
                    with mock.patch.object(
                        response,
                        "close",
                        side_effect=OSError("synthetic close failure"),
                    ):
                        with self.assertRaisesRegex(
                            (RuntimeError, ValueError), "could not be closed"
                        ):
                            handler.http_error_302(
                                Request("https://api.github.com/synthetic"),
                                response,
                                302,
                                "Found",
                                headers,
                            )

    def test_approved_documentation_redirect_discards_even_over_cap_body(self) -> None:
        module = load_source("scripts/audit_official_docs.py")
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                handler = module.ApprovedRedirectHandler(("docs.github.com",))
                headers = HTTPMessage()
                headers["Location"] = "https://docs.github.com/en/rest/git/trees"
                final = mock.MagicMock()
                final.__enter__.return_value = final
                final.read.return_value = b"Valid final page"
                final.geturl.return_value = headers["Location"]
                parent = mock.Mock(open=mock.Mock(return_value=final))
                handler.add_parent(parent)
                with BytesIO(b"x" * (module.MAX_RESPONSE_BYTES + 1)) as response:

                    def dispatch(request: Request, *, timeout: float) -> object:
                        request.timeout = timeout  # type: ignore[attr-defined]
                        return getattr(handler, f"http_error_{status}")(
                            request, response, status, "Redirect", headers
                        )

                    with (
                        mock.patch.object(
                            response,
                            "read",
                            side_effect=AssertionError("must not drain"),
                        ),
                        mock.patch.object(
                            module,
                            "build_opener",
                            return_value=mock.Mock(open=dispatch),
                        ),
                    ):
                        result = module.read_document(
                            headers["Location"], ("docs.github.com",)
                        )
                    self.assertEqual(result, (headers["Location"], "Valid final page"))
                    self.assertTrue(response.closed)
                    final.read.assert_called_once_with(module.MAX_RESPONSE_BYTES + 1)
                    parent.open.assert_called_once()

    def test_direct_documentation_refusal_closes_before_url_error(self) -> None:
        module = load_source("scripts/audit_official_docs.py")
        for url in (
            "http://docs.github.com/x",
            "https://redirect.invalid/x",
            "https://docs.github.com:bad/x",
        ):
            with self.subTest(url=url), BytesIO(b"Unused") as response:
                handler = module.ApprovedRedirectHandler(("docs.github.com",))
                with self.assertRaises(module.AuditError):
                    handler.redirect_request(
                        Request("https://docs.github.com/x"),
                        response,
                        302,
                        "Found",
                        Message(),
                        url,
                    )
                self.assertTrue(response.closed)
        with BytesIO(b"Unused") as response:
            with mock.patch.object(response, "close", side_effect=OSError("synthetic")):
                with self.assertRaisesRegex(module.AuditError, "could not be closed"):
                    handler.redirect_request(
                        Request("https://docs.github.com/x"),
                        response,
                        302,
                        "Found",
                        Message(),
                        "http://docs.github.com/x",
                    )

    def test_approved_redirect_preserves_native_loop_limit_and_no_response_case(
        self,
    ) -> None:
        module = load_source("scripts/audit_official_docs.py")
        handler = module.ApprovedRedirectHandler(("docs.github.com",))
        request = Request("https://docs.github.com/start")
        request.timeout = 30  # type: ignore[attr-defined]

        def follow(request: Request, *, timeout: float) -> Request:
            request.timeout = timeout  # type: ignore[attr-defined]
            return request

        parent = mock.Mock(open=mock.Mock(side_effect=follow))
        handler.add_parent(parent)
        for index in range(handler.max_redirections + 1):
            headers = HTTPMessage()
            headers["Location"] = f"https://docs.github.com/page-{index}"
            if index < handler.max_redirections:
                request = handler.http_error_302(request, None, 302, "Found", headers)
            else:
                with self.assertRaises(HTTPError):
                    handler.http_error_302(request, None, 302, "Found", headers)
        self.assertEqual(parent.open.call_count, handler.max_redirections)


if __name__ == "__main__":
    unittest.main()
