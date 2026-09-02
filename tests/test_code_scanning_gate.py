from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "check_code_scanning_alerts.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.check_code_scanning_alerts", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load check_code_scanning_alerts.py")
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


class FakeResponse(BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def alert(number: int, *, path: str | None = "scripts/example.py") -> dict[str, object]:
    return {
        "number": number,
        "tool": {"name": "CodeQL"},
        "rule": {"id": "py/example"},
        "most_recent_instance": {"location": {"path": path}},
    }


class CodeScanningGateTests(unittest.TestCase):
    def write_allowlist(self, root: Path, entries: object) -> Path:
        path = root / "allowlist.json"
        path.write_text(
            json.dumps({"schema-version": 2, "allowlist": entries}), encoding="utf-8"
        )
        return path

    def test_allowlist_is_strict_and_matches_exact_alert_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_allowlist(
                Path(directory),
                [
                    {
                        "number": 1,
                        "tool": "CodeQL",
                        "rule": "py/example",
                        "path": "scripts/example.py",
                        "reason": "Reviewed.",
                    }
                ],
            )
            selectors = gate.load_allowlist(path)
        self.assertEqual(
            gate.unapproved_alerts(
                (gate.Alert(1, "CodeQL", "py/example", "scripts/example.py"),),
                selectors,
            ),
            [],
        )
        self.assertEqual(
            len(
                gate.unapproved_alerts(
                    (gate.Alert(2, "CodeQL", "py/example", "other.py"),), selectors
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                gate.unapproved_alerts(
                    (gate.Alert(2, "CodeQL", "py/example", "scripts/example.py"),),
                    selectors,
                )
            ),
            1,
        )

    def test_checked_in_allowlist_approves_reviewed_default_branch_checkout(
        self,
    ) -> None:
        allowlist_path = PLUGIN_ROOT / ".github" / "code-scanning-allowlist.json"
        allowlist_text = allowlist_path.read_text(encoding="utf-8")
        selectors = gate.load_allowlist(allowlist_path)

        allowlist = json.loads(allowlist_text)
        reasons_by_path = {
            entry["path"]: entry["reason"] for entry in allowlist["allowlist"]
        }
        code_scanning_reason = reasons_by_path[
            ".github/workflows/code-scanning-gate.yml"
        ]
        self.assertIn("trusted default branch", code_scanning_reason)
        self.assertNotIn("github.event.pull_request.base.sha", code_scanning_reason)
        self.assertIn(
            "github.event.pull_request.base.sha",
            reasons_by_path[".github/workflows/pr-template.yml"],
        )

        self.assertEqual(
            gate.unapproved_alerts(
                (
                    gate.Alert(
                        18,
                        "Scorecard",
                        "DangerousWorkflowID",
                        ".github/workflows/code-scanning-gate.yml",
                    ),
                ),
                selectors,
            ),
            [],
        )

    def test_allowlist_rejects_unsafe_or_ambiguous_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path_value in (
                "../escape",
                r"scripts\example.py",
                "scripts/./example.py",
                "scripts//example.py",
                "C:/example.py",
                "scripts/C:example.py",
            ):
                path_entries = [
                    {
                        "number": 1,
                        "tool": "CodeQL",
                        "rule": "x",
                        "path": path_value,
                        "reason": "x",
                    }
                ]
                with self.subTest(path_value=path_value):
                    with self.assertRaisesRegex(gate.GateError, "allowlist path"):
                        gate.load_allowlist(self.write_allowlist(root, path_entries))
            for entries in (
                [
                    {
                        "number": 1,
                        "tool": "CodeQL",
                        "rule": "x",
                        "path": None,
                        "reason": "x",
                    }
                ]
                * 2,
                [
                    {
                        "number": 0,
                        "tool": "CodeQL",
                        "rule": "x",
                        "path": None,
                        "reason": "x",
                    }
                ],
                [
                    {
                        "number": True,
                        "tool": "CodeQL",
                        "rule": "x",
                        "path": None,
                        "reason": "x",
                    }
                ],
                [{"tool": "CodeQL", "rule": "x", "path": None}],
            ):
                with self.subTest(entries=entries):
                    with self.assertRaises(gate.GateError):
                        gate.load_allowlist(self.write_allowlist(root, entries))

    def test_allowlist_rejects_unreadable_schema_and_non_list_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(gate.GateError, "could not read"):
                gate.load_allowlist(root / "missing.json")
            for document, message in (
                ({"schema-version": 1, "allowlist": []}, "schema-version"),
                ({"schema-version": 2, "allowlist": {}}, "must be a list"),
            ):
                path = root / "allowlist.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with (
                    self.subTest(document=document),
                    self.assertRaisesRegex(gate.GateError, message),
                ):
                    gate.load_allowlist(path)

    def test_allowlist_rejects_duplicate_json_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowlist.json"
            path.write_text(
                '{"schema-version": 2, "schema-version": 2, "allowlist": []}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(gate.GateError, "duplicate JSON member"):
                gate.load_allowlist(path)

    def test_allowlist_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowlist.json"
            path.write_bytes(b" " * (gate.MAX_ALLOWLIST_BYTES + 1))

            with self.assertRaisesRegex(gate.GateError, "exceeds the .*byte limit"):
                gate.load_allowlist(path)

    def test_allowlist_rejects_too_many_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowlist.json"
            path.write_text(
                json.dumps(
                    {
                        "schema-version": 2,
                        "allowlist": [
                            {
                                "number": index + 1,
                                "tool": "CodeQL",
                                "rule": "rule",
                                "path": None,
                                "reason": "reason",
                            }
                            for index in range(gate.MAX_ALLOWLIST_ENTRIES + 1)
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(gate.GateError, "exceeds the .*entry limit"):
                gate.load_allowlist(path)

    def test_allowlist_converts_recursion_errors_to_gate_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowlist.json"
            path.write_text("{}", encoding="utf-8")

            for error in (RecursionError("too deep"), ValueError("too many digits")):
                with (
                    self.subTest(error=type(error).__name__),
                    mock.patch.object(gate.json, "loads", side_effect=error),
                    self.assertRaisesRegex(gate.GateError, "could not read"),
                ):
                    gate.load_allowlist(path)

    def test_api_client_bounds_and_authenticates_requests(self) -> None:
        with mock.patch.object(
            gate.GITHUB_API_OPENER, "open", return_value=FakeResponse(b"[]")
        ) as open_url:
            self.assertEqual(
                gate.api_json("https://api.github.com/example", "token"), []
            )
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        with mock.patch.object(
            gate.GITHUB_API_OPENER,
            "open",
            return_value=FakeResponse(b"x" * (gate.MAX_RESPONSE_BYTES + 1)),
        ):
            with self.assertRaisesRegex(gate.GateError, "exceeds"):
                gate.api_json("https://api.github.com/example", "token")

        with (
            mock.patch.object(
                gate.GITHUB_API_OPENER, "open", return_value=FakeResponse(b"[]")
            ),
            mock.patch.object(
                gate.json, "loads", side_effect=ValueError("too many digits")
            ),
            self.assertRaisesRegex(gate.GateError, "not valid JSON"),
        ):
            gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(
            gate.GITHUB_API_OPENER, "open", side_effect=URLError("offline")
        ):
            with self.assertRaisesRegex(gate.TransientGateError, "transiently"):
                gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(
            gate.GITHUB_API_OPENER,
            "open",
            side_effect=HTTPError(
                "https://api.github.com/example", 500, "error", Message(), None
            ),
        ):
            with self.assertRaisesRegex(gate.TransientGateError, "transiently"):
                gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(
            gate.GITHUB_API_OPENER,
            "open",
            side_effect=HTTPError(
                "https://api.github.com/example", 408, "error", Message(), None
            ),
        ):
            with self.assertRaisesRegex(gate.TransientGateError, "transiently"):
                gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(
            gate.GITHUB_API_OPENER,
            "open",
            side_effect=HTTPError(
                "https://api.github.com/example", 401, "error", Message(), None
            ),
        ):
            with self.assertRaisesRegex(gate.GateError, "request failed"):
                gate.api_json("https://api.github.com/example", "token")
        for payload in (b"not JSON", b'{"name":"first","name":"second"}'):
            with (
                self.subTest(payload=payload),
                mock.patch.object(
                    gate.GITHUB_API_OPENER, "open", return_value=FakeResponse(payload)
                ),
            ):
                with self.assertRaisesRegex(gate.GateError, "not valid JSON"):
                    gate.api_json("https://api.github.com/example", "token")

    def test_api_client_rejects_redirects_before_following_them(self) -> None:
        handler = gate.RejectRedirectHandler()

        with self.assertRaisesRegex(gate.GateError, "redirects are not allowed"):
            handler.redirect_request(
                Request("https://api.github.com/example"),
                None,
                302,
                "Found",
                Message(),
                "https://example.test/receive-token",
            )

    def test_analyses_and_waiting_handle_invalid_retry_and_timeout_states(self) -> None:
        with mock.patch.object(
            gate,
            "api_json",
            side_effect=[
                [
                    {
                        "commit_sha": "a" * 40,
                        "tool": {"name": "CodeQL"},
                        "category": "/language:python",
                    },
                    {
                        "commit_sha": "a" * 40,
                        "tool": {"name": "Scorecard"},
                        "category": "/language:actions",
                    },
                    {"commit_sha": "different"},
                ],
                [
                    {
                        "commit_sha": "a" * 40,
                        "tool": {"name": "CodeQL"},
                        "category": "/language:actions",
                    },
                    {
                        "commit_sha": "a" * 40,
                        "tool": {"name": "CodeQL"},
                        "category": "/language:python",
                    },
                ],
                {"unexpected": "mapping"},
            ],
        ):
            self.assertFalse(
                gate.analyses_ready(
                    "owner/repo",
                    "refs/pull/1/merge",
                    "a" * 40,
                    "t",
                    frozenset({"/language:actions", "/language:python"}),
                )
            )
            self.assertTrue(
                gate.analyses_ready(
                    "owner/repo",
                    "refs/pull/1/merge",
                    "a" * 40,
                    "t",
                    frozenset({"/language:actions", "/language:python"}),
                )
            )
            with self.assertRaisesRegex(gate.GateError, "must be a list"):
                gate.analyses_ready(
                    "owner/repo", "refs/pull/1/merge", "a" * 40, "t", frozenset()
                )
        with (
            mock.patch.object(gate, "analyses_ready", side_effect=[False, True]),
            mock.patch.object(gate, "time") as clock,
        ):
            gate.wait_for_analyses(
                "owner/repo",
                "refs/pull/1/merge",
                "a" * 40,
                "t",
                2,
                1.5,
                frozenset({"/language:python"}),
            )
        clock.sleep.assert_called_once_with(1.5)
        with (
            mock.patch.object(
                gate,
                "analyses_ready",
                side_effect=[gate.TransientGateError("temporary"), True],
            ),
            mock.patch.object(gate, "time") as clock,
        ):
            gate.wait_for_analyses(
                "owner/repo",
                "refs/pull/1/merge",
                "a" * 40,
                "t",
                2,
                1.5,
                frozenset({"/language:python"}),
            )
        clock.sleep.assert_called_once_with(1.5)
        with mock.patch.object(gate, "analyses_ready", return_value=False):
            with self.assertRaisesRegex(gate.GateError, "were not queryable"):
                gate.wait_for_analyses(
                    "owner/repo",
                    "refs/pull/1/merge",
                    "a" * 40,
                    "t",
                    1,
                    0,
                    frozenset({"/language:python"}),
                )

    def test_analyses_accept_an_equivalent_event_merge_commit(self) -> None:
        analyses = [
            {
                "commit_sha": "a" * 40,
                "tool": {"name": "CodeQL"},
                "category": "/language:actions",
            },
            {
                "commit_sha": "a" * 40,
                "tool": {"name": "CodeQL"},
                "category": "/language:python",
            },
            {
                "commit_sha": "b" * 40,
                "tool": {"name": "CodeQL"},
                "category": "/language:actions",
            },
            {
                "commit_sha": "b" * 40,
                "tool": {"name": "CodeQL"},
                "category": "/language:python",
            },
        ]
        with (
            mock.patch.object(gate, "api_json", return_value=analyses),
            mock.patch.object(
                gate, "merge_commit_has_parents", side_effect=[False, True]
            ) as matches,
        ):
            self.assertTrue(
                gate.analyses_ready(
                    "owner/repo",
                    "refs/pull/1/merge",
                    "c" * 40,
                    "token",
                    frozenset({"/language:actions", "/language:python"}),
                    ("d" * 40, "e" * 40),
                )
            )
        self.assertEqual(matches.call_count, 2)

    def test_analyses_paginates_before_declaring_a_commit_unavailable(self) -> None:
        first_page = [
            {
                "commit_sha": f"{index:040x}",
                "tool": {"name": "CodeQL"},
                "category": "/language:python",
            }
            for index in range(100)
        ]
        target = "a" * 40
        second_page = [
            {
                "commit_sha": target,
                "tool": {"name": "CodeQL"},
                "category": "/language:python",
            }
        ]
        with mock.patch.object(
            gate, "api_json", side_effect=[first_page, second_page]
        ) as api_json:
            self.assertTrue(
                gate.analyses_ready(
                    "owner/repo",
                    "refs/heads/main",
                    target,
                    "token",
                    frozenset({"/language:python"}),
                )
            )

        self.assertIn("page=1", api_json.call_args_list[0].args[0])
        self.assertIn("page=2", api_json.call_args_list[1].args[0])

    def test_analyses_rejects_unbounded_pagination(self) -> None:
        page = [
            {
                "commit_sha": f"{index:040x}",
                "tool": {"name": "CodeQL"},
                "category": "/language:python",
            }
            for index in range(100)
        ]
        with (
            mock.patch.object(gate, "MAX_ANALYSIS_PAGES", 1),
            mock.patch.object(gate, "api_json", return_value=page),
            self.assertRaisesRegex(gate.GateError, "more than"),
        ):
            gate.analyses_ready(
                "owner/repo",
                "refs/heads/main",
                "a" * 40,
                "token",
                frozenset({"/language:python"}),
            )

    def test_merge_commit_parent_validation_rejects_malformed_responses(self) -> None:
        with mock.patch.object(
            gate,
            "api_json",
            return_value={"parents": [{"sha": "b" * 40}, {"sha": "c" * 40}]},
        ):
            self.assertTrue(
                gate.merge_commit_has_parents(
                    "owner/repo", "a" * 40, "token", ("b" * 40, "c" * 40)
                )
            )
        for response, message in (
            ([], "response must be an object"),
            ({"parents": {}}, "parents must be a list"),
            ({"parents": [{"sha": "invalid"}]}, "must contain commit SHAs"),
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(gate, "api_json", return_value=response),
            ):
                with self.assertRaisesRegex(gate.GateError, message):
                    gate.merge_commit_has_parents(
                        "owner/repo", "a" * 40, "token", ("b" * 40, "c" * 40)
                    )

    def test_open_alerts_handles_pagination_and_rejects_invalid_responses(self) -> None:
        first = [alert(index) for index in range(100)]
        with mock.patch.object(
            gate, "api_json", side_effect=[first, [alert(101, path=None)]]
        ):
            alerts = gate.open_alerts("owner/repo", "refs/heads/main", "token")
        self.assertEqual((alerts[0].number, alerts[-1].path), (0, None))
        with mock.patch.object(gate, "api_json", return_value={"not": "a list"}):
            with self.assertRaisesRegex(gate.GateError, "must be a list"):
                gate.open_alerts("owner/repo", "refs/heads/main", "token")
        for response, message in (
            (["not an object"], "must be an object"),
            (
                [
                    {
                        "number": 1,
                        "tool": None,
                        "rule": {},
                        "most_recent_instance": {},
                    }
                ],
                "stable identity",
            ),
            (
                [
                    {
                        "number": 1,
                        "tool": {"name": "CodeQL"},
                        "rule": {"id": "py/example"},
                        "most_recent_instance": {"location": {"path": 42}},
                    }
                ],
                "path must be text",
            ),
            ([{**alert(1), "number": "one"}], "number must be an integer"),
            ([{**alert(1), "number": True}], "number must be an integer"),
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(gate, "api_json", return_value=response),
            ):
                with self.assertRaisesRegex(gate.GateError, message):
                    gate.open_alerts("owner/repo", "refs/heads/main", "token")
        with (
            mock.patch.object(gate, "MAX_PAGES", 1),
            mock.patch.object(gate, "api_json", return_value=first),
        ):
            with self.assertRaisesRegex(gate.GateError, "more than"):
                gate.open_alerts("owner/repo", "refs/heads/main", "token")

    def test_open_alert_waiting_retries_transient_api_errors(self) -> None:
        with (
            mock.patch.object(
                gate,
                "open_alerts",
                side_effect=[gate.TransientGateError("temporary"), ()],
            ),
            mock.patch.object(gate, "time") as clock,
        ):
            self.assertEqual(
                gate.wait_for_open_alerts(
                    "owner/repo", "refs/heads/main", "token", attempts=2, delay=1.5
                ),
                (),
            )
        clock.sleep.assert_called_once_with(1.5)
        with mock.patch.object(
            gate, "open_alerts", side_effect=gate.TransientGateError("temporary")
        ):
            with self.assertRaisesRegex(gate.GateError, "were not queryable"):
                gate.wait_for_open_alerts(
                    "owner/repo", "refs/heads/main", "token", attempts=1, delay=0
                )

    def test_pull_request_merge_sha_waits_for_github_to_finish_mergeability(
        self,
    ) -> None:
        with (
            mock.patch.object(
                gate,
                "api_json",
                side_effect=[
                    {"mergeable": None},
                    {"mergeable": True},
                    {"object": {"sha": "a" * 40}},
                ],
            ),
            mock.patch.object(gate, "analyses_ready", return_value=True) as ready,
            mock.patch.object(gate, "time") as clock,
        ):
            ref, sha = gate.wait_for_pull_request_analyses(
                "owner/repo",
                "42",
                "token",
                attempts=2,
                delay=3.0,
                expected_categories=frozenset({"/language:python"}),
                expected_parents=("b" * 40, "c" * 40),
            )

        self.assertEqual((ref, sha), ("refs/pull/42/merge", "a" * 40))
        ready.assert_called_once_with(
            "owner/repo",
            "refs/pull/42/merge",
            "a" * 40,
            "token",
            frozenset({"/language:python"}),
            ("b" * 40, "c" * 40),
        )
        clock.sleep.assert_called_once_with(3.0)

    def test_pull_request_analysis_waiting_retries_and_times_out(self) -> None:
        with (
            mock.patch.object(gate, "pull_request_merge_sha", return_value="a" * 40),
            mock.patch.object(gate, "analyses_ready", side_effect=[False, True]),
            mock.patch.object(gate, "time") as clock,
        ):
            self.assertEqual(
                gate.wait_for_pull_request_analyses(
                    "owner/repo",
                    "42",
                    "token",
                    attempts=2,
                    delay=2.0,
                    expected_categories=frozenset({"/language:python"}),
                    expected_parents=("b" * 40, "c" * 40),
                ),
                ("refs/pull/42/merge", "a" * 40),
            )
        clock.sleep.assert_called_once_with(2.0)
        with (
            mock.patch.object(
                gate,
                "pull_request_merge_sha",
                side_effect=[gate.TransientGateError("temporary"), "a" * 40],
            ),
            mock.patch.object(gate, "analyses_ready", return_value=True),
            mock.patch.object(gate, "time") as clock,
        ):
            self.assertEqual(
                gate.wait_for_pull_request_analyses(
                    "owner/repo",
                    "42",
                    "token",
                    attempts=2,
                    delay=2.0,
                    expected_categories=frozenset({"/language:python"}),
                    expected_parents=("b" * 40, "c" * 40),
                ),
                ("refs/pull/42/merge", "a" * 40),
            )
        clock.sleep.assert_called_once_with(2.0)
        with mock.patch.object(gate, "pull_request_merge_sha", return_value=None):
            with self.assertRaisesRegex(gate.GateError, "were not queryable"):
                gate.wait_for_pull_request_analyses(
                    "owner/repo",
                    "42",
                    "token",
                    attempts=1,
                    delay=0,
                    expected_categories=frozenset({"/language:python"}),
                    expected_parents=("b" * 40, "c" * 40),
                )

    def test_pull_request_merge_sha_rejects_unmergeable_or_malformed_responses(
        self,
    ) -> None:
        for response, message in (
            ({"mergeable": False}, "no mergeable"),
            ([], "must be an object"),
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(gate, "api_json", return_value=response),
            ):
                with self.assertRaisesRegex(gate.GateError, message):
                    gate.pull_request_merge_sha("owner/repo", "42", "token")

    def test_pull_request_merge_sha_retries_an_invalid_transient_sha(self) -> None:
        with mock.patch.object(
            gate,
            "api_json",
            side_effect=[{"mergeable": True}, {"object": {"sha": "invalid"}}],
        ):
            self.assertIsNone(gate.pull_request_merge_sha("owner/repo", "42", "token"))

        with mock.patch.object(
            gate,
            "api_json",
            side_effect=[{"mergeable": True}, []],
        ):
            with self.assertRaisesRegex(gate.GateError, "merge ref response"):
                gate.pull_request_merge_sha("owner/repo", "42", "token")

    def test_main_waits_for_analyses_and_fails_only_unapproved_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = self.write_allowlist(
                Path(directory),
                [
                    {
                        "number": 1,
                        "tool": "CodeQL",
                        "rule": "py/example",
                        "path": "scripts/example.py",
                        "reason": "Reviewed.",
                    }
                ],
            )
            arguments = [
                "--repository",
                "owner/repo",
                "--ref",
                "refs/pull/1/merge",
                "--sha",
                "a" * 40,
                "--token",
                "token",
                "--allowlist",
                str(allowlist),
                "--expected-codeql-category",
                "/language:python",
                "--delay-seconds",
                "0",
            ]
            with (
                mock.patch.object(gate, "wait_for_analyses") as wait,
                mock.patch.object(
                    gate,
                    "wait_for_open_alerts",
                    return_value=(
                        gate.Alert(1, "CodeQL", "py/example", "scripts/example.py"),
                    ),
                ),
            ):
                self.assertEqual(gate.main(arguments), 0)
            wait.assert_called_once()
            with (
                mock.patch.object(gate, "wait_for_analyses"),
                mock.patch.object(
                    gate,
                    "wait_for_open_alerts",
                    return_value=(gate.Alert(2, "CodeQL", "py/new", "scripts/new.py"),),
                ),
            ):
                self.assertEqual(gate.main(arguments), 1)

    def test_main_uses_pull_request_polling_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = self.write_allowlist(Path(directory), [])
            arguments = [
                "--repository",
                "owner/repo",
                "--pull-request",
                "42",
                "--base-sha",
                "a" * 40,
                "--head-sha",
                "b" * 40,
                "--token",
                "token",
                "--allowlist",
                str(allowlist),
                "--expected-codeql-category",
                "/language:python",
            ]
            with (
                mock.patch.object(
                    gate,
                    "wait_for_pull_request_analyses",
                    return_value=("refs/pull/42/merge", "a" * 40),
                ) as wait,
                mock.patch.object(gate, "wait_for_open_alerts", return_value=()),
            ):
                self.assertEqual(gate.main(arguments), 0)
            wait.assert_called_once_with(
                "owner/repo",
                "42",
                "token",
                12,
                5.0,
                frozenset({"/language:python"}),
                ("a" * 40, "b" * 40),
            )

            arguments[arguments.index("42")] = "0"
            self.assertEqual(gate.main(arguments), 2)
            arguments[arguments.index("0")] = "42"
            arguments[arguments.index("a" * 40)] = "invalid"
            self.assertEqual(gate.main(arguments), 2)

    def test_main_rejects_invalid_arguments_and_script_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = self.write_allowlist(Path(directory), [])
            base = [
                "--token",
                "token",
                "--allowlist",
                str(allowlist),
                "--expected-codeql-category",
                "/language:python",
            ]
            self.assertEqual(gate.main(["--repository", "invalid", *base]), 2)
            self.assertEqual(
                gate.main(
                    [
                        "--repository",
                        "owner/repo",
                        "--token",
                        "token",
                        "--allowlist",
                        str(allowlist),
                    ]
                ),
                2,
            )
            self.assertEqual(
                gate.main(["--repository", "owner/repo", "--token", "", *base[2:]]),
                2,
            )
            self.assertEqual(
                gate.main(
                    [
                        "--repository",
                        "owner/repo",
                        "--attempts",
                        "0",
                        *base,
                    ]
                ),
                2,
            )
            self.assertEqual(
                gate.main(
                    [
                        "--repository",
                        "owner/repo",
                        "--ref",
                        "refs/heads/main",
                        "--sha",
                        "invalid",
                        *base,
                    ]
                ),
                2,
            )
        with mock.patch.object(
            sys, "argv", [str(SCRIPT_PATH), "--repository", "invalid"]
        ):
            with self.assertRaises(SystemExit) as error:
                runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
        self.assertEqual(error.exception.code, 2)
