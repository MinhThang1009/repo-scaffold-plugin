from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "render_pr_body_evidence.py"
SPEC = importlib.util.spec_from_file_location("render_pr_body_evidence", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load render_pr_body_evidence.py")
renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer
SPEC.loader.exec_module(renderer)


HEAD = "a" * 40
BASE = "b" * 40
REPOSITORY = "MinhThang1009/repo-scaffold-plugin"
BODY = (
    "<!-- repo-scaffold:pr-template=bugfix -->\n\n"
    "## Purpose\n\nOld purpose.\n\n"
    "## Root cause\n\nOld cause.\n\n"
    "## Key changes\n\nOld changes.\n\n"
    "## Verification\n\nOld verification.\n\n"
    "## Required checklist\n\n"
    "<!-- repo-scaffold:required-checklist:start -->\n"
    "- [x] Existing evidence\n"
    "<!-- repo-scaffold:required-checklist:end -->\n\n"
    "## If applicable\n\n"
    "<!-- repo-scaffold:optional-checklist:start -->\n"
    "- [ ] Optional evidence\n"
    "<!-- repo-scaffold:optional-checklist:end -->\n\n"
    "## Related issue\n\nOld issue.\n"
)
PR = {
    "title": "fix: generated body",
    "state": "open",
    "head": {"sha": HEAD, "repo": {"full_name": REPOSITORY}},
    "base": {"sha": BASE},
    "body": BODY,
}
COMMITS = [
    [
        {
            "sha": HEAD,
            "commit": {"message": "fix: current subject\n\nCause evidence\nCloses #42"},
        }
    ]
]
FILES = [
    [
        {
            "filename": "scripts/example.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "changes": 3,
        }
    ]
]
CHECKS = [
    [
        {
            "check_runs": [
                {
                    "id": 7,
                    "name": "quality",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
    ]
]


class RenderPullRequestBodyTests(unittest.TestCase):
    def test_render_binds_head_and_replaces_every_narrative_section(self) -> None:
        rendered = renderer.render_dynamic_body(BODY, PR, COMMITS, FILES, CHECKS)

        self.assertNotIn(renderer.MANAGED_BODY_MARKER, rendered)
        self.assertIn("Current subject.", rendered)
        self.assertIn("Evidence.", rendered)
        self.assertIn("## Purpose\n\n", rendered)
        self.assertIn("## Key changes\n\n- Current subject.", rendered)
        self.assertIn("## Verification\n\n- Automated checks for this head", rendered)
        self.assertIn("#42", rendered)
        self.assertNotIn("Old purpose", rendered)
        self.assertIn("- [x] Existing evidence", rendered)
        self.assertIn("- [ ] Optional evidence", rendered)

    def test_render_uses_structured_commit_summary_fields(self) -> None:
        structured = [
            [
                {
                    "sha": HEAD,
                    "commit": {
                        "message": (
                            "fix: concise summary\n\n"
                            "Why: Protect the mutation boundary.\n"
                            "Root cause: Indeterminate evidence was treated as actionable.\n"
                            "Changes: Add a fail-closed guard.\n"
                            "Verification: Run the focused regression suite."
                        )
                    },
                }
            ]
        ]
        rendered = renderer.render_dynamic_body(BODY, PR, structured, FILES, CHECKS)

        self.assertIn("Protect the mutation boundary", rendered)
        self.assertIn("Indeterminate evidence was treated as actionable", rendered)
        self.assertIn("Add a fail-closed guard", rendered)
        self.assertIn("Run the focused regression suite", rendered)

    def test_render_is_deterministic_and_preserves_crlf(self) -> None:
        crlf_body = BODY.replace("\n", "\r\n")
        crlf_pr = {**PR, "body": crlf_body}
        rendered = renderer.render_dynamic_body(
            crlf_body, crlf_pr, COMMITS, FILES, CHECKS
        )

        self.assertEqual(
            renderer.render_dynamic_body(
                rendered, {**crlf_pr, "body": rendered}, COMMITS, FILES, CHECKS
            ),
            rendered,
        )
        self.assertNotIn("\n", rendered.replace("\r\n", ""))
        no_trailing = BODY.rstrip("\n")
        renderer.render_dynamic_body(
            no_trailing, {**PR, "body": no_trailing}, COMMITS, FILES, CHECKS
        )
        legacy_body = BODY.replace(
            "<!-- repo-scaffold:pr-template=bugfix -->\n",
            "<!-- repo-scaffold:pr-template=bugfix -->\n"
            + renderer.MANAGED_BODY_MARKER
            + "\n",
        )
        renderer.render_dynamic_body(
            legacy_body,
            {**PR, "body": legacy_body},
            COMMITS,
            FILES,
            CHECKS,
        )

    def test_render_accepts_direct_check_run_pages_and_empty_files(self) -> None:
        checks = [{"id": 1, "name": "pending", "status": "queued", "conclusion": None}]
        rendered = renderer.render_dynamic_body(BODY, PR, COMMITS[0], [], checks)

        self.assertIn("pending", rendered)
        self.assertIn("No changed files", rendered)

    def test_render_rejects_stale_or_untrusted_evidence(self) -> None:
        cases = (
            ({**PR, "body": "different"}, COMMITS, FILES, CHECKS, "body changed"),
            ({**PR, "state": "closed"}, COMMITS, FILES, CHECKS, "must remain open"),
            (PR, [[{**COMMITS[0][0], "sha": BASE}]], FILES, CHECKS, "latest commit"),
            (
                PR,
                COMMITS,
                [
                    [
                        {**FILES[0][0], "filename": "same"},
                        {**FILES[0][0], "filename": "same"},
                    ]
                ],
                CHECKS,
                "duplicate",
            ),
            (
                PR,
                COMMITS,
                FILES,
                [[{"id": 1, "name": "x", "status": "unknown", "conclusion": None}]],
                "unknown status",
            ),
        )
        for pr, commits, files, checks, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    renderer.render_dynamic_body(BODY, pr, commits, files, checks)

    def test_render_rejects_malformed_body_and_duplicate_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "trusted template marker"):
            renderer.render_dynamic_body("body\n", PR, COMMITS, FILES, CHECKS)
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            renderer._unique_object([("a", 1), ("a", 2)])
        with self.assertRaisesRegex(ValueError, "pagination shape"):
            renderer._flatten_pages([1], "items")

    def test_validation_boundaries_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "could not read"):
            renderer._read_bytes(Path("missing-evidence.json"), "evidence", 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized"
            oversized.write_bytes(b"x" * 11)
            with self.assertRaisesRegex(ValueError, "safety cap"):
                renderer._read_bytes(oversized, "evidence", 10)
            invalid = root / "invalid"
            invalid.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                renderer.read_body(invalid)
        with self.assertRaisesRegex(ValueError, "unsupported carriage"):
            renderer._line_ending("a\rb")
        with self.assertRaisesRegex(ValueError, "mixes CRLF"):
            renderer._line_ending("a\r\nb\n")
        with self.assertRaisesRegex(ValueError, "JSON array"):
            renderer._flatten_pages({}, "items")
        with self.assertRaisesRegex(ValueError, "non-object item"):
            renderer._flatten_pages([[1]], "items")
        with self.assertRaisesRegex(ValueError, "must be text"):
            renderer._bounded_text(None, "value")
        self.assertTrue(
            len(renderer._bounded_text("x" * 3000, "value")) <= renderer.MAX_TEXT + 1
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            renderer._bounded_text("", "value", allow_empty=False)
        with self.assertRaisesRegex(ValueError, "40 hexadecimal"):
            renderer._validate_sha("short", "sha")
        with self.assertRaisesRegex(ValueError, "OWNER/REPOSITORY"):
            renderer._validate_repository("unsafe", "repository")
        with self.assertRaisesRegex(ValueError, "valid head"):
            renderer._head_and_base({})
        with self.assertRaisesRegex(ValueError, "head repository"):
            renderer._head_and_base({"head": {"sha": HEAD}, "base": {"sha": BASE}})
        with self.assertRaisesRegex(ValueError, "missing"):
            renderer._validate_commits([], HEAD)
        with self.assertRaisesRegex(ValueError, "duplicate SHA"):
            renderer._validate_commits([COMMITS[0][0], COMMITS[0][0]], HEAD)
        with self.assertRaisesRegex(ValueError, "metadata"):
            renderer._validate_commits([[{"sha": HEAD}]], HEAD)
        with self.assertRaisesRegex(ValueError, "exceed"):
            renderer._validate_files([{"filename": "x"}] * (renderer.MAX_FILES + 1))
        with self.assertRaisesRegex(ValueError, "non-negative"):
            renderer._validate_files(
                [
                    [
                        {
                            "filename": "x",
                            "status": "modified",
                            "additions": -1,
                            "deletions": 0,
                            "changes": 1,
                        }
                    ]
                ]
            )
        with self.assertRaisesRegex(ValueError, "unknown status"):
            renderer._validate_files(
                [
                    [
                        {
                            "filename": "x",
                            "status": "unknown",
                            "additions": 0,
                            "deletions": 0,
                            "changes": 0,
                        }
                    ]
                ]
            )
        with self.assertRaisesRegex(ValueError, "check_runs array"):
            renderer._validate_checks([{"check_runs": None}])
        with self.assertRaisesRegex(ValueError, "non-object item"):
            renderer._validate_checks([{"check_runs": [1]}])
        with self.assertRaisesRegex(ValueError, "exceed"):
            renderer._validate_checks(
                [{"name": "x", "status": "queued", "conclusion": None}]
                * (renderer.MAX_CHECK_RUNS + 1)
            )
        with self.assertRaisesRegex(ValueError, "identifier"):
            renderer._validate_checks(
                [[{"id": [], "name": "x", "status": "queued", "conclusion": None}]]
            )
        duplicate_check = {"id": 1, "name": "x", "status": "queued", "conclusion": None}
        with self.assertRaisesRegex(ValueError, "duplicate run"):
            renderer._validate_checks([[duplicate_check, duplicate_check]])
        self.assertEqual(
            renderer._commit_details([{"sha": HEAD, "message": "subject"}]),
            ["- No structured detail was supplied in the commit metadata."],
        )
        self.assertIn(
            "The core issue addressed",
            renderer._root_cause_lines([{"sha": HEAD, "message": "subject"}])[0],
        )
        self.assertIn(
            "core rationale from the commit metadata",
            renderer._root_cause_lines(
                [{"sha": HEAD, "message": "subject\n\nUnstructured detail"}]
            )[0],
        )
        self.assertEqual(
            renderer._structured_values(
                [{"sha": HEAD, "message": "subject\nWhy: same\nWhy: same"}],
                "purpose",
            ),
            ["same"],
        )
        self.assertEqual(
            len(
                renderer._changes_summary(
                    [
                        {"sha": HEAD, "message": "fix: current subject"},
                        {"sha": HEAD, "message": "fix: current subject"},
                    ],
                    FILES[0],
                )
            ),
            2,
        )
        self.assertIn(
            "Synchronize",
            renderer._diff_theme(
                [
                    {"filename": ".github/workflows/ci.yml"},
                    {"filename": "tests/test_render.py"},
                ]
            ),
        )
        self.assertEqual(renderer._diff_theme([{"filename": "src/app.py"}]), "")
        self.assertEqual(
            renderer._verification_summary([], []),
            ["- No check runs were returned for this head."],
        )
        self.assertEqual(renderer._summary_text([]), "the available commit evidence")
        self.assertIn("; and", renderer._summary_text(["one", "two"]))
        self.assertIn(
            "more commit(s)", renderer._summary_text(["one", "two", "three", "four"])
        )
        duplicate_issue = {"sha": HEAD, "message": "subject\nCloses #1 and #1"}
        self.assertEqual(len(renderer._issue_lines([duplicate_issue])), 1)
        with self.assertRaisesRegex(ValueError, "unterminated"):
            renderer._remove_protocol_lines([renderer.HEAD_START_MARKER])
        self.assertEqual(
            renderer._remove_protocol_lines(
                [renderer.HEAD_START_MARKER, "old metadata", renderer.HEAD_END_MARKER]
            ),
            [],
        )
        with mock.patch.object(renderer, "MAX_BODY_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "generated"):
                renderer.render_dynamic_body(BODY, PR, COMMITS, FILES, CHECKS)
            with self.assertRaisesRegex(ValueError, "generated"):
                renderer.write_body(
                    Path(tempfile.gettempdir()) / "body-output.md", "body"
                )
        with self.assertRaisesRegex(ValueError, "could not write"):
            renderer.write_body(Path(tempfile.gettempdir()), "body")

    def test_cli_renders_and_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = root / "body.md"
            pr = root / "pr.json"
            commits = root / "commits.json"
            files = root / "files.json"
            checks = root / "checks.json"
            output = root / "updated.md"
            body.write_bytes(BODY.encode("utf-8"))
            pr.write_text(json.dumps(PR), encoding="utf-8")
            commits.write_text(json.dumps(COMMITS), encoding="utf-8")
            files.write_text(json.dumps(FILES), encoding="utf-8")
            checks.write_text(json.dumps(CHECKS), encoding="utf-8")
            stdout = StringIO()
            with redirect_stdout(stdout):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("current PR evidence", stdout.getvalue())
            self.assertIn("Current subject.", output.read_text(encoding="utf-8"))

            pr.write_text("{", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 1)
            self.assertIn("valid JSON", stderr.getvalue())
            pr.write_text("[]", encoding="utf-8")
            with redirect_stderr(StringIO()):
                result = renderer.main(
                    [
                        "--body-file",
                        str(body),
                        "--pr-file",
                        str(pr),
                        "--commits-file",
                        str(commits),
                        "--files-file",
                        str(files),
                        "--checks-file",
                        str(checks),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 1)

    def test_script_entrypoint_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                name: root / f"{name}.json"
                for name in ("pr", "commits", "files", "checks")
            }
            root.joinpath("body.md").write_bytes(BODY.encode("utf-8"))
            paths["pr"].write_text(json.dumps(PR), encoding="utf-8")
            paths["commits"].write_text(json.dumps(COMMITS), encoding="utf-8")
            paths["files"].write_text(json.dumps(FILES), encoding="utf-8")
            paths["checks"].write_text(json.dumps(CHECKS), encoding="utf-8")
            argv = [
                str(SCRIPT_PATH),
                "--body-file",
                str(root / "body.md"),
                "--pr-file",
                str(paths["pr"]),
                "--commits-file",
                str(paths["commits"]),
                "--files-file",
                str(paths["files"]),
                "--checks-file",
                str(paths["checks"]),
                "--output",
                str(root / "output.md"),
            ]
            old_argv = sys.argv
            sys.argv = argv
            try:
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(SCRIPT_PATH), run_name="__main__")
            finally:
                sys.argv = old_argv
            self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
