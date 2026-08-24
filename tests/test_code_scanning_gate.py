from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.error import URLError


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
            json.dumps({"schema-version": 1, "allowlist": entries}), encoding="utf-8"
        )
        return path

    def test_allowlist_is_strict_and_matches_exact_alert_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_allowlist(
                Path(directory),
                [
                    {
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

    def test_allowlist_rejects_unsafe_or_ambiguous_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for entries in (
                [{"tool": "CodeQL", "rule": "x", "path": "../escape", "reason": "x"}],
                [{"tool": "CodeQL", "rule": "x", "path": None, "reason": "x"}] * 2,
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
                ({"schema-version": 2, "allowlist": []}, "schema-version"),
                ({"schema-version": 1, "allowlist": {}}, "must be a list"),
            ):
                path = root / "allowlist.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with (
                    self.subTest(document=document),
                    self.assertRaisesRegex(gate.GateError, message),
                ):
                    gate.load_allowlist(path)

    def test_api_client_bounds_and_authenticates_requests(self) -> None:
        with mock.patch.object(
            gate, "urlopen", return_value=FakeResponse(b"[]")
        ) as open_url:
            self.assertEqual(
                gate.api_json("https://api.github.com/example", "token"), []
            )
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        with mock.patch.object(
            gate,
            "urlopen",
            return_value=FakeResponse(b"x" * (gate.MAX_RESPONSE_BYTES + 1)),
        ):
            with self.assertRaisesRegex(gate.GateError, "exceeds"):
                gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(gate, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(gate.GateError, "request failed"):
                gate.api_json("https://api.github.com/example", "token")
        with mock.patch.object(gate, "urlopen", return_value=FakeResponse(b"not JSON")):
            with self.assertRaisesRegex(gate.GateError, "not valid JSON"):
                gate.api_json("https://api.github.com/example", "token")

    def test_analyses_and_waiting_handle_invalid_retry_and_timeout_states(self) -> None:
        with mock.patch.object(
            gate,
            "api_json",
            side_effect=[
                [{"commit_sha": "a" * 40}, {"commit_sha": "different"}],
                {"unexpected": "mapping"},
            ],
        ):
            self.assertFalse(
                gate.analyses_ready("owner/repo", "refs/pull/1/merge", "a" * 40, "t", 2)
            )
            with self.assertRaisesRegex(gate.GateError, "must be a list"):
                gate.analyses_ready("owner/repo", "refs/pull/1/merge", "a" * 40, "t", 2)
        with (
            mock.patch.object(gate, "analyses_ready", side_effect=[False, True]),
            mock.patch.object(gate, "time") as clock,
        ):
            gate.wait_for_analyses(
                "owner/repo", "refs/pull/1/merge", "a" * 40, "t", 2, 1.5, 2
            )
        clock.sleep.assert_called_once_with(1.5)
        with mock.patch.object(gate, "analyses_ready", return_value=False):
            with self.assertRaisesRegex(gate.GateError, "were not queryable"):
                gate.wait_for_analyses(
                    "owner/repo", "refs/pull/1/merge", "a" * 40, "t", 1, 0, 2
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

    def test_pull_request_merge_sha_waits_for_github_to_finish_mergeability(
        self,
    ) -> None:
        with (
            mock.patch.object(
                gate,
                "api_json",
                side_effect=[
                    {"mergeable": None},
                    {"mergeable": True, "merge_commit_sha": "a" * 40},
                ],
            ),
            mock.patch.object(gate, "analyses_ready", return_value=True) as ready,
            mock.patch.object(gate, "time") as clock,
        ):
            ref, sha = gate.wait_for_pull_request_analyses(
                "owner/repo", "42", "token", attempts=2, delay=3.0, expected=2
            )

        self.assertEqual((ref, sha), ("refs/pull/42/merge", "a" * 40))
        ready.assert_called_once_with(
            "owner/repo", "refs/pull/42/merge", "a" * 40, "token", 2
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
                    "owner/repo", "42", "token", attempts=2, delay=2.0, expected=2
                ),
                ("refs/pull/42/merge", "a" * 40),
            )
        clock.sleep.assert_called_once_with(2.0)
        with mock.patch.object(gate, "pull_request_merge_sha", return_value=None):
            with self.assertRaisesRegex(gate.GateError, "were not queryable"):
                gate.wait_for_pull_request_analyses(
                    "owner/repo", "42", "token", attempts=1, delay=0, expected=2
                )

    def test_pull_request_merge_sha_rejects_unmergeable_or_invalid_responses(
        self,
    ) -> None:
        for response, message in (
            ({"mergeable": False}, "no mergeable"),
            ({"mergeable": True, "merge_commit_sha": "invalid"}, "invalid mergeable"),
            ([], "must be an object"),
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(gate, "api_json", return_value=response),
            ):
                with self.assertRaisesRegex(gate.GateError, message):
                    gate.pull_request_merge_sha("owner/repo", "42", "token")

    def test_main_waits_for_analyses_and_fails_only_unapproved_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = self.write_allowlist(
                Path(directory),
                [
                    {
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
                "--delay-seconds",
                "0",
            ]
            with (
                mock.patch.object(gate, "wait_for_analyses") as wait,
                mock.patch.object(
                    gate,
                    "open_alerts",
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
                    "open_alerts",
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
                "--token",
                "token",
                "--allowlist",
                str(allowlist),
            ]
            with (
                mock.patch.object(
                    gate,
                    "wait_for_pull_request_analyses",
                    return_value=("refs/pull/42/merge", "a" * 40),
                ) as wait,
                mock.patch.object(gate, "open_alerts", return_value=()),
            ):
                self.assertEqual(gate.main(arguments), 0)
            wait.assert_called_once_with("owner/repo", "42", "token", 12, 5.0, 2)

            arguments[arguments.index("42")] = "0"
            self.assertEqual(gate.main(arguments), 2)

    def test_main_rejects_invalid_arguments_and_script_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            allowlist = self.write_allowlist(Path(directory), [])
            base = ["--token", "token", "--allowlist", str(allowlist)]
            self.assertEqual(gate.main(["--repository", "invalid", *base]), 2)
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
